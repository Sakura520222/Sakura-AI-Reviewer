"""AI审查引擎"""

from typing import Dict, List, Optional, Any
from openai import AsyncOpenAI
from loguru import logger
import json

from backend.core.config import get_settings, get_strategy_config

settings = get_settings()
strategy_config = get_strategy_config()


class AIReviewer:
    """AI审查器"""

    def __init__(self):
        self.client = AsyncOpenAI(
            base_url=settings.openai_api_base, api_key=settings.openai_api_key
        )
        
        # 定义可用工具
        self.tools = [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "读取指定文件的完整内容，用于理解代码实现细节",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file_path": {
                                "type": "string",
                                "description": "要读取的文件路径（相对于项目根目录）"
                            }
                        },
                        "required": ["file_path"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "list_directory",
                    "description": "列出指定目录下的文件和子目录",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "directory": {
                                "type": "string",
                                "description": "要列出的目录路径（相对于项目根目录）"
                            }
                        },
                        "required": ["directory"]
                    }
                }
            }
        ]

    async def review_pr(self, context: Dict[str, any], strategy: str) -> Dict[str, any]:
        """审查PR"""
        try:
            logger.info(f"开始AI审查，策略: {strategy}")

            # 获取策略配置
            strategy_config_data = strategy_config.get_strategy(strategy)
            system_prompt = strategy_config_data.get("prompt", "")

            # 构建用户消息
            user_message = self._build_user_message(context, strategy)

            # 调用AI API
            response = await self.client.chat.completions.create(
                model=settings.openai_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                temperature=settings.openai_temperature,
                max_tokens=settings.openai_max_tokens,
            )

            # 提取回复
            review_text = response.choices[0].message.content

            # 解析审查结果
            result = self._parse_review_result(review_text, strategy)

            logger.info(f"AI审查完成，策略: {strategy}")
            return result

        except Exception as e:
            logger.error(f"AI审查时出错: {e}", exc_info=True)
            raise

    def _build_user_message(self, context: Dict[str, any], strategy: str) -> str:
        """构建用户消息"""
        message_parts = [
            "## PR信息",
            f"- 策略: {context.get('strategy_name', strategy)}",
            f"- 文件数: {context.get('file_count', 0)}",
            f"- 变更行数: {context.get('total_changes', 0)}",
            "",
        ]

        # 添加文件信息
        files = context.get("files", [])
        if files:
            message_parts.append("## 代码变更")

            for i, file in enumerate(files, 1):
                message_parts.append(f"\n### {i}. {file['path']}")
                message_parts.append(f"- 状态: {file['status']}")
                message_parts.append(
                    f"- 变更: +{file.get('additions', 0)} -{file.get('deletions', 0)}"
                )

                # 添加patch
                if file.get("patch"):
                    patch = file["patch"]
                    # 限制patch长度
                    if len(patch) > 3000:
                        patch = patch[:3000] + "\n... (truncated)"
                    message_parts.append(f"\n```diff\n{patch}\n```")

        # 添加剩余文件信息
        if context.get("remaining_files"):
            message_parts.append(
                f"\n注意: 还有 {context['remaining_files']} 个文件未显示"
            )

        # 添加文件摘要（针对large策略）
        if context.get("file_summary"):
            message_parts.append("\n## 文件变更摘要")
            for file in context["file_summary"]:
                message_parts.append(
                    f"- {file['path']}: {file['status']} ({file['changes']} 行)"
                )

        return "\n".join(message_parts)

    def _parse_review_result(self, review_text: str, strategy: str) -> Dict[str, any]:
        """解析审查结果"""
        result = {
            "summary": review_text,
            "comments": [],
            "overall_score": None,
            "issues": {"critical": [], "major": [], "minor": [], "suggestions": []},
        }

        try:
            # 对于深度审查策略，尝试提取评分
            if strategy == "deep":
                # 查找评分模式
                import re

                score_match = re.search(r"评分[：:]\s*(\d+)", review_text)
                if score_match:
                    result["overall_score"] = int(score_match.group(1))

            # 提取结构化评论
            lines = review_text.split("\n")
            current_section = None
            current_content = []

            for line in lines:
                # 检查是否为标题
                if line.strip().startswith("##") or line.strip().startswith("#"):
                    if current_section and current_content:
                        self._add_comment_from_section(
                            result, current_section, current_content
                        )
                    current_section = line.strip()
                    current_content = []
                else:
                    current_content.append(line)

            # 处理最后一个部分
            if current_section and current_content:
                self._add_comment_from_section(result, current_section, current_content)

            # 如果没有提取到结构化评论，将整个文本作为摘要
            if not result["comments"]:
                result["summary"] = review_text

        except Exception as e:
            logger.warning(f"解析审查结果时出错: {e}")
            result["summary"] = review_text

        return result

    def _add_comment_from_section(
        self, result: Dict[str, any], section: str, content: List[str]
    ):
        """从章节中添加评论"""
        content_text = "\n".join(content).strip()
        if not content_text:
            return

        # 根据章节标题确定严重程度
        section_lower = section.lower()

        severity = "suggestion"
        if "严重" in section or "critical" in section_lower or "🔴" in section:
            severity = "critical"
        elif "重要" in section or "major" in section_lower or "🟡" in section:
            severity = "major"
        elif "优化" in section or "suggestion" in section_lower or "💡" in section:
            severity = "suggestion"
        elif "做得好" in section or "✅" in section:
            # 正面反馈，不作为问题
            return

        # 提取列表项
        import re

        items = re.split(r"^[\-\*]\s*", content_text, flags=re.MULTILINE)

        for item in items:
            item = item.strip()
            if item and len(item) > 10:  # 忽略太短的项
                result["comments"].append(
                    {"content": item, "severity": severity, "type": "overall"}
                )
                # 修复：直接使用 severity，不要加 "s"
                if severity in result["issues"]:
                    result["issues"][severity].append(item)

    async def review_file(
        self, file_path: str, patch: str, strategy: str
    ) -> Dict[str, any]:
        """审查单个文件"""
        try:
            # 获取策略配置
            strategy_config_data = strategy_config.get_strategy(strategy)
            system_prompt = strategy_config_data.get("prompt", "")

            # 构建文件审查消息
            user_message = f"""请审查以下文件的代码变更：

文件: {file_path}

```diff
{patch}
```

请指出潜在的问题和改进建议。"""

            # 调用AI API
            response = await self.client.chat.completions.create(
                model=settings.openai_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                temperature=settings.openai_temperature,
                max_tokens=2000,
            )

            review_text = response.choices[0].message.content

            return {"file_path": file_path, "review": review_text}

        except Exception as e:
            logger.error(f"审查文件 {file_path} 时出错: {e}")
            return {"file_path": file_path, "review": f"审查失败: {str(e)}"}

    async def review_pr_with_tools(
        self, 
        context: Dict[str, any], 
        strategy: str,
        repo: Any,
        pr: Any
    ) -> Dict[str, any]:
        """使用函数工具审查PR，支持AI主动查看文件
        
        Args:
            context: 审查上下文
            strategy: 审查策略
            repo: GitHub仓库对象
            pr: GitHub PR对象
            
        Returns:
            审查结果字典
        """
        try:
            logger.info(f"开始AI审查（带工具支持），策略: {strategy}")

            # 获取策略配置
            strategy_config_data = strategy_config.get_strategy(strategy)
            system_prompt = self._build_system_prompt_with_tools(
                strategy_config_data.get("prompt", ""),
                context
            )

            # 构建用户消息
            user_message = self._build_user_message_with_tools(context, strategy)

            # 初始化消息列表
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ]

            # 多轮对话循环
            max_iterations = 10  # 防止无限循环
            iteration = 0
            
            while iteration < max_iterations:
                iteration += 1
                
                # 调用AI API
                response = await self.client.chat.completions.create(
                    model=settings.openai_model,
                    messages=messages,
                    tools=self.tools,
                    tool_choice="auto",
                    temperature=settings.openai_temperature,
                    max_tokens=settings.openai_max_tokens,
                )

                # 检查是否有工具调用
                tool_calls = response.choices[0].message.tool_calls
                
                if not tool_calls:
                    # AI完成了审查，返回结果
                    review_text = response.choices[0].message.content
                    result = self._parse_review_result(review_text, strategy)
                    logger.info(f"AI审查完成（使用了{iteration}轮对话），策略: {strategy}")
                    return result

                # 处理工具调用
                assistant_message = response.choices[0].message
                assistant_msg_dict = {
                    "role": "assistant",
                    "content": assistant_message.content,
                    "tool_calls": tool_calls
                }
                
                # DeepSeek-R1 特有：必须包含 reasoning_content
                if hasattr(assistant_message, 'reasoning_content') and assistant_message.reasoning_content:
                    assistant_msg_dict["reasoning_content"] = assistant_message.reasoning_content
                
                messages.append(assistant_msg_dict)

                # 执行每个工具调用
                for tool_call in tool_calls:
                    try:
                        result = await self._handle_tool_call(tool_call, repo, pr)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": json.dumps(result, ensure_ascii=False)
                        })
                        logger.info(f"执行工具 {tool_call.function.name}: {tool_call.function.arguments}")
                    except Exception as e:
                        logger.error(f"执行工具 {tool_call.function.name} 失败: {e}")
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": json.dumps({"error": str(e)})
                        })

            # 超过最大迭代次数，强制返回
            logger.warning(f"超过最大迭代次数 {max_iterations}，强制结束")
            last_response = await self.client.chat.completions.create(
                model=settings.openai_model,
                messages=messages,
                temperature=settings.openai_temperature,
                max_tokens=settings.openai_max_tokens,
            )
            review_text = last_response.choices[0].message.content
            return self._parse_review_result(review_text, strategy)

        except Exception as e:
            logger.error("AI审查（带工具）时出错: {}", str(e), exc_info=True)
            raise

    async def _handle_tool_call(self, tool_call: Any, repo: Any, pr: Any) -> Dict[str, any]:
        """处理AI的工具调用请求
        
        Args:
            tool_call: OpenAI工具调用对象
            repo: GitHub仓库对象
            pr: GitHub PR对象
            
        Returns:
            工具执行结果
        """
        function_name = tool_call.function.name
        arguments = json.loads(tool_call.function.arguments)

        if function_name == "read_file":
            return await self._tool_read_file(arguments["file_path"], repo, pr)
        elif function_name == "list_directory":
            return await self._tool_list_directory(arguments["directory"], repo, pr)
        else:
            return {"error": f"未知工具: {function_name}"}

    async def _tool_read_file(self, file_path: str, repo: Any, pr: Any) -> Dict[str, any]:
        """读取文件内容的工具实现
        
        Args:
            file_path: 文件路径
            repo: GitHub仓库对象
            pr: GitHub PR对象
            
        Returns:
            文件内容
        """
        try:
            # 检查是否应该跳过该路径
            skip_paths = strategy_config.get_file_filters().get("skip_paths", [])
            for skip_path in skip_paths:
                if file_path.startswith(skip_path.rstrip('/')):
                    logger.info(f"跳过读取文件（在skip_paths中）: {file_path}")
                    return {
                        "file_path": file_path,
                        "error": f"该路径在跳过列表中，无法访问"
                    }
            
            # 获取PR基础分支的文件内容
            content_file = repo.get_contents(file_path, pr.base.sha)
            
            if content_file.size > 100000:  # 限制100KB
                return {
                    "error": "文件过大",
                    "size": content_file.size,
                    "content": None
                }
            
            # 解码文件内容
            content = content_file.decoded_content.decode('utf-8')
            
            return {
                "file_path": file_path,
                "content": content,
                "size": content_file.size
            }
            
        except Exception as e:
            logger.error(f"读取文件 {file_path} 失败: {e}")
            return {
                "file_path": file_path,
                "error": f"无法读取文件: {str(e)}"
            }

    async def _tool_list_directory(self, directory: str, repo: Any, pr: Any) -> Dict[str, any]:
        """列出目录内容的工具实现
        
        Args:
            directory: 目录路径
            repo: GitHub仓库对象
            pr: GitHub PR对象
            
        Returns:
            目录内容列表
        """
        try:
            # 检查是否应该跳过该路径
            skip_paths = strategy_config.get_file_filters().get("skip_paths", [])
            for skip_path in skip_paths:
                if directory.startswith(skip_path.rstrip('/')):
                    logger.info(f"跳过列出目录（在skip_paths中）: {directory}")
                    return {
                        "directory": directory,
                        "error": f"该路径在跳过列表中，无法访问",
                        "items": [],
                        "count": 0
                    }
            
            # 获取目录内容
            contents = repo.get_contents(directory, pr.base.sha)
            
            if isinstance(contents, list):
                items = []
                # 过滤掉skip_paths中的项目
                for item in contents:
                    should_skip = False
                    for skip_path in skip_paths:
                        if item.path.startswith(skip_path.rstrip('/')):
                            should_skip = True
                            break
                    
                    if not should_skip:
                        items.append({
                            "name": item.name,
                            "path": item.path,
                            "type": item.type,
                            "size": item.size if item.type == "file" else None
                        })
                
                return {
                    "directory": directory,
                    "items": items,
                    "count": len(items),
                    "filtered": len(contents) - len(items) if len(items) < len(contents) else 0
                }
            else:
                # 单个文件 - 也需要检查skip_paths
                for skip_path in skip_paths:
                    if contents.path.startswith(skip_path.rstrip('/')):
                        return {
                            "directory": directory,
                            "error": f"该路径在跳过列表中",
                            "items": [],
                            "count": 0
                        }
                
                # 单个文件
                return {
                    "directory": directory,
                    "items": [{
                        "name": contents.name,
                        "path": contents.path,
                        "type": contents.type,
                        "size": contents.size
                    }],
                    "count": 1
                }
                
        except Exception as e:
            logger.error(f"列出目录 {directory} 失败: {e}")
            return {
                "directory": directory,
                "error": f"无法列出目录: {str(e)}",
                "items": [],
                "count": 0
            }

    def _build_system_prompt_with_tools(self, base_prompt: str, context: Dict[str, any]) -> str:
        """构建包含工具说明的系统提示词"""
        tools_instruction = """

## 可用工具

你可以使用以下工具来更好地理解代码：

1. **read_file**: 读取指定文件的完整内容
   - 使用场景：需要理解某个函数的完整实现、查看配置文件详情、了解依赖模块
   - 参数：file_path（文件路径）

2. **list_directory**: 列出目录中的文件和子目录
   - 使用场景：了解模块结构、查找相关文件、探索项目组织
   - 参数：directory（目录路径）

## 使用建议

- 优先审查PR中变更的文件
- 当需要理解依赖关系时，使用 read_file 查看相关文件
- 当需要了解模块结构时，使用 list_directory 查看目录
- 合理使用工具，避免不必要的文件读取
- 工具调用会消耗额外的token，请按需使用

## 项目结构

以下是项目的完整目录结构，可以帮助你了解项目组织：

```
{project_structure}
```
""".format(project_structure="\n".join(context.get("project_structure", [])))

        return base_prompt + tools_instruction

    def _build_user_message_with_tools(self, context: Dict[str, any], strategy: str) -> str:
        """构建包含工具说明的用户消息"""
        message_parts = [
            "## PR信息",
            f"- 策略: {context.get('strategy_name', strategy)}",
            f"- 文件数: {context.get('file_count', 0)}",
            f"- 变更行数: {context.get('total_changes', 0)}",
            "",
            "## 可用工具",
            "你可以使用以下工具来更好地理解代码：",
            "- `read_file`: 读取任意文件的完整内容",
            "- `list_directory`: 列出目录中的文件",
            "",
            "请根据需要使用这些工具来获取更多上下文信息。",
            "",
        ]

        # 添加文件信息
        files = context.get("files", [])
        if files:
            message_parts.append("## 代码变更")

            for i, file in enumerate(files, 1):
                message_parts.append(f"\n### {i}. {file['path']}")
                message_parts.append(f"- 状态: {file['status']}")
                message_parts.append(
                    f"- 变更: +{file.get('additions', 0)} -{file.get('deletions', 0)}"
                )

                # 添加patch
                if file.get("patch"):
                    patch = file["patch"]
                    # 限制patch长度
                    if len(patch) > 3000:
                        patch = patch[:3000] + "\n... (truncated)"
                    message_parts.append(f"\n```diff\n{patch}\n```")

        # 添加剩余文件信息
        if context.get("remaining_files"):
            message_parts.append(
                f"\n注意: 还有 {context['remaining_files']} 个文件未显示"
            )

        # 添加文件摘要（针对large策略）
        if context.get("file_summary"):
            message_parts.append("\n## 文件变更摘要")
            for file in context["file_summary"]:
                message_parts.append(
                    f"- {file['path']}: {file['status']} ({file['changes']} 行)"
                )

        message_parts.append("\n请开始审查，并根据需要使用工具查看相关文件。")

        return "\n".join(message_parts)
