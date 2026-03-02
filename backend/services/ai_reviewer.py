"""AI审查引擎"""

from typing import Dict, List, Optional, Any
from openai import AsyncOpenAI
from loguru import logger
import json
import asyncio

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

    async def _call_ai_with_retry(self, **kwargs) -> Any:
        """带重试机制的AI API调用
        
        处理空响应、异常、网络错误等情况
        
        Returns:
            OpenAI API响应对象
            
        Raises:
            Exception: 重试3次后仍然失败
        """
        max_retries = 3
        retry_delay = 3  # 秒
        
        for attempt in range(max_retries):
            try:
                # 调用AI API
                response = await self.client.chat.completions.create(**kwargs)
                
                # 检查空响应
                if not response.choices or not response.choices[0].message.content:
                    if attempt < max_retries - 1:
                        logger.warning(
                            f"AI返回空响应，{retry_delay}秒后重试 ({attempt + 1}/{max_retries})"
                        )
                        await asyncio.sleep(retry_delay)
                        continue
                    else:
                        logger.error("AI返回空响应，已达最大重试次数")
                        raise Exception("AI返回空响应，已达最大重试次数")
                
                # 成功返回
                return response
                
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(
                        f"AI调用失败: {e}，{retry_delay}秒后重试 ({attempt + 1}/{max_retries})"
                    )
                    await asyncio.sleep(retry_delay)
                else:
                    logger.error(f"AI调用失败，已达最大重试次数: {e}")
                    raise

    async def review_pr(self, context: Dict[str, any], strategy: str) -> Dict[str, any]:
        """审查PR"""
        try:
            logger.info(f"开始AI审查，策略: {strategy}")

            # 获取策略配置
            strategy_config_data = strategy_config.get_strategy(strategy)
            system_prompt = strategy_config_data.get("prompt", "")

            # 构建用户消息
            user_message = self._build_user_message(context, strategy)

            # 调用AI API（带重试）
            response = await self._call_ai_with_retry(
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

            # 调用AI API（带重试）
            response = await self._call_ai_with_retry(
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
                
                # 调用AI API（带重试）
                response = await self._call_ai_with_retry(
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
            last_response = await self._call_ai_with_retry(
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

    async def recommend_labels(
        self,
        context: Dict[str, any],
        available_labels: Dict[str, Dict[str, Any]],
        pr_info: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """推荐PR标签

        Args:
            context: 审查上下文
            available_labels: 可用的标签字典
            pr_info: PR信息（包含标题、描述等）

        Returns:
            推荐标签列表，格式：[{"name": str, "confidence": float, "reason": str}]
        """
        try:
            logger.info("开始AI标签推荐分析")

            # 构建系统提示词
            system_prompt = """你是一个专业的代码审查助手，擅长根据代码变更的内容和性质为Pull Request推荐合适的标签。

## 标签推荐原则

1. **准确性**: 仔细分析代码变更的实际内容，不要仅凭文件名或路径判断
2. **多维度**: 可以同时推荐多个标签，覆盖不同维度
3. **置信度**: 为每个标签给出0-1之间的置信度分数
   - 0.8-1.0: 非常确定，明显符合该标签特征
   - 0.6-0.8: 较为确定，很可能符合
   - 0.4-0.6: 可能符合，需要更多信息确认
   - 0.2-0.4: 有一定可能，但不确定
   - 0.0-0.2: 仅作建议参考
4. **理由说明**: 为每个推荐标签提供简洁的理由

## 标签类型参考

- **bug**: 修复错误、缺陷、边界条件问题
- **enhancement**: 新功能、功能增强、新增API
- **refactor**: 代码重构、结构优化（非功能性变更）
- **performance**: 性能优化、缓存改进、算法优化
- **documentation**: 文档更新、README、注释
- **test**: 测试代码、测试用例、测试修复
- **dependencies**: 依赖更新、包管理
- **ci**: CI/CD配置、工作流、自动化
- **style**: 代码风格、格式化、linting
- **build**: 构建配置、编译脚本

## 输出格式

请以JSON格式返回推荐结果：

```json
{
  "labels": [
    {
      "name": "标签名称",
      "confidence": 0.85,
      "reason": "推荐理由说明"
    }
  ]
}
```

**重要输出要求**：
- 请仅输出 JSON 格式结果，不要包含任何解释文字或 Markdown 标记
- 确保以 '{' 开头，以 '}' 结尾
- 不要添加 ```json 或 ``` 等标记
- 只推荐列表中存在的标签
- 最多推荐3-5个标签
- 置信度必须是0-1之间的数字
- 理由说明要简洁具体
"""

            # 构建用户消息
            user_message = self._build_label_recommendation_message(
                context, available_labels, pr_info
            )

            # 调用AI API（带重试）
            response = await self._call_ai_with_retry(
                model=settings.openai_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                temperature=0.3,  # 使用较低的温度以获得更一致的结果
                max_tokens=1500,
            )

            # 提取响应
            recommendation_text = response.choices[0].message.content
            
            # 记录完整响应用于调试
            logger.debug(f"AI标签推荐完整响应:\n{recommendation_text}")
            logger.info(f"AI标签推荐响应长度: {len(recommendation_text)} 字符")

            # 解析推荐结果
            recommendations = self._parse_label_recommendation(recommendation_text)

            logger.info(f"AI标签推荐完成，共 {len(recommendations)} 个推荐")
            return recommendations

        except Exception as e:
            logger.error(f"AI标签推荐失败: {e}", exc_info=True)
            return []

    def _build_label_recommendation_message(
        self,
        context: Dict[str, any],
        available_labels: Dict[str, Dict[str, Any]],
        pr_info: Dict[str, Any],
    ) -> str:
        """构建标签推荐的用户消息"""
        lines = [
            "## Pull Request 信息",
            f"- 标题: {pr_info.get('title', 'N/A')}",
            f"- 作者: {pr_info.get('author', 'N/A')}",
            f"- 分支: {pr_info.get('branch', 'N/A')} → {pr_info.get('base_branch', 'N/A')}",
            "",
        ]

        # 添加可用标签
        lines.append("## 可用的标签")
        for label_name, label_info in available_labels.items():
            desc = label_info.get("description", "")
            lines.append(f"- **{label_name}**: {desc}")

        # 添加代码变更信息
        files = context.get("files", [])
        if files:
            lines.append("\n## 代码变更")

            for i, file in enumerate(files[:10], 1):  # 限制前10个文件
                lines.append(f"\n### {i}. {file['path']}")
                lines.append(f"- 状态: {file['status']}")
                lines.append(f"- 变更: +{file.get('additions', 0)} -{file.get('deletions', 0)}")

                # 添加简化的patch（只显示前200字符）
                if file.get("patch"):
                    patch = file["patch"]
                    if len(patch) > 200:
                        patch = patch[:200] + "\n... (truncated)"
                    lines.append(f"\n```diff\n{patch}\n```")

            if len(files) > 10:
                lines.append(f"\n*还有 {len(files) - 10} 个文件未显示*")

        # 添加统计信息
        lines.append("\n## 变更统计")
        lines.append(f"- 文件数: {context.get('file_count', 0)}")
        lines.append(f"- 总变更行数: {context.get('total_changes', 0)}")

        lines.append("\n请分析以上信息，推荐最合适的标签。")

        return "\n".join(lines)

    def _parse_label_recommendation(self, response_text: str) -> List[Dict[str, Any]]:
        """解析标签推荐响应"""
        recommendations = []

        try:
            # 检查响应是否为空
            if not response_text or not response_text.strip():
                logger.warning("AI返回空响应")
                return []

            # 清理响应文本
            text = response_text.strip()
            
            # 尝试提取JSON代码块
            if "```json" in text:
                start = text.find("```json") + 7
                end = text.find("```", start)
                if end > start:
                    json_str = text[start:end].strip()
                    data = json.loads(json_str)
                else:
                    # 没有结束标记，尝试从 ```json 后面全部解析
                    json_str = text[start:].strip()
                    data = json.loads(json_str)
            elif "```" in text:
                # 尝试提取普通代码块
                start = text.find("```") + 3
                end = text.find("```", start)
                if end > start:
                    json_str = text[start:end].strip()
                    data = json.loads(json_str)
                else:
                    # 没有结束标记
                    json_str = text[start:].strip()
                    data = json.loads(json_str)
            else:
                # 直接解析整个响应
                data = json.loads(text)

            # 提取标签列表
            if isinstance(data, dict) and "labels" in data:
                for item in data["labels"]:
                    recommendations.append({
                        "name": item.get("name", ""),
                        "confidence": float(item.get("confidence", 0.5)),
                        "reason": item.get("reason", ""),
                    })
            elif isinstance(data, list):
                # 直接是标签列表
                for item in data:
                    recommendations.append({
                        "name": item.get("name", ""),
                        "confidence": float(item.get("confidence", 0.5)),
                        "reason": item.get("reason", ""),
                    })

            logger.info(f"成功解析 {len(recommendations)} 个标签推荐")
            return recommendations

        except json.JSONDecodeError as e:
            logger.error(f"JSON解析失败: {e}")
            # 尝试文本解析作为后备
            return self._parse_text_label_recommendation(response_text)
        except Exception as e:
            logger.error(f"解析标签推荐失败: {e}", exc_info=True)
            return []

    def _parse_text_label_recommendation(self, text: str) -> List[Dict[str, Any]]:
        """从文本中解析标签推荐（后备方案）"""
        recommendations = []
        lines = text.split("\n")

        for line in lines:
            line = line.strip()
            # 查找格式：- 标签名 (置信度) - 理由
            if line.startswith("-") or line.startswith("*"):
                parts = line[1:].strip().split("(", 1)
                if len(parts) > 0:
                    label_name = parts[0].strip()
                    
                    confidence = 0.5
                    reason = ""
                    
                    if len(parts) > 1:
                        rest = parts[1]
                        # 提取置信度
                        if ")" in rest:
                            conf_str = rest.split(")")[0].strip()
                            try:
                                # 处理百分比格式
                                if "%" in conf_str:
                                    confidence = float(conf_str.replace("%", "").strip()) / 100
                                else:
                                    confidence = float(conf_str)
                            except ValueError:
                                pass
                        
                        # 提取理由
                        if "-" in rest:
                            reason_parts = rest.split("-", 1)
                            if len(reason_parts) > 1:
                                reason = reason_parts[1].strip()
                    
                    if label_name:
                        recommendations.append({
                            "name": label_name,
                            "confidence": min(max(confidence, 0.0), 1.0),
                            "reason": reason,
                        })

        return recommendations
