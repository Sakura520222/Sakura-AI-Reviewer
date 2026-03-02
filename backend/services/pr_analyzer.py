"""PR分析服务"""

from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from loguru import logger

from backend.core.config import get_settings, get_strategy_config
from backend.core.github_app import GitHubAppClient

settings = get_settings()
strategy_config = get_strategy_config()


@dataclass
class PRFileInfo:
    """文件变更信息"""

    path: str
    status: str  # added, modified, deleted, renamed
    additions: int
    deletions: int
    changes: int
    patch: Optional[str] = None
    is_code_file: bool = False


@dataclass
class PRAnalysis:
    """PR分析结果"""

    pr_id: int
    pr_number: int
    repo_full_name: str

    # 统计信息
    total_files: int
    total_additions: int
    total_deletions: int
    total_changes: int

    # 代码文件统计
    code_files: List[PRFileInfo]
    code_file_count: int
    code_changes: int

    # 策略判断
    strategy: str
    should_skip: bool
    skip_reason: Optional[str] = None


class PRAnalyzer:
    """PR分析器"""

    def __init__(self):
        self.github_app = GitHubAppClient()

    async def analyze_pr(self, pr_info: Dict[str, any]) -> PRAnalysis:
        """分析PR并返回分析结果"""
        try:
            # 获取GitHub客户端
            client = self.github_app.get_repo_client(
                pr_info["repo_owner"], pr_info["repo_name"]
            )
            if not client:
                raise Exception("无法获取GitHub客户端")

            # 获取仓库和PR
            repo = client.get_repo(pr_info["repo_full_name"])
            pr = repo.get_pull(pr_info["pr_number"])

            logger.info(
                f"开始分析PR: {pr_info['repo_full_name']}#{pr_info['pr_number']}"
            )

            # 获取所有文件变更
            files = pr.get_files()
            file_list = list(files)

            # 分析文件
            code_files = []
            total_additions = 0
            total_deletions = 0
            total_changes = 0

            for file in file_list:
                file_info = PRFileInfo(
                    path=file.filename,
                    status=file.status,
                    additions=file.additions,
                    deletions=file.deletions,
                    changes=file.changes,
                    patch=file.patch if hasattr(file, "patch") else None,
                    is_code_file=strategy_config.is_code_file(file.filename),
                )

                # 检查是否应该跳过
                if strategy_config.should_skip_file(file.filename):
                    logger.debug(f"跳过文件: {file.filename}")
                    continue

                total_additions += file.additions
                total_deletions += file.deletions
                total_changes += file.changes

                # 只收集代码文件
                if file_info.is_code_file:
                    code_files.append(file_info)

            # 计算代码变更
            code_changes = sum(f.changes for f in code_files)

            # 判断是否应该跳过审查
            should_skip, skip_reason = self._should_skip_review(
                len(code_files), code_changes, len(file_list)
            )

            # 确定审查策略
            if should_skip:
                strategy = "skip"
            else:
                strategy = strategy_config.determine_strategy(
                    len(code_files), code_changes
                )

            analysis = PRAnalysis(
                pr_id=pr_info["pr_id"],
                pr_number=pr_info["pr_number"],
                repo_full_name=pr_info["repo_full_name"],
                total_files=len(file_list),
                total_additions=total_additions,
                total_deletions=total_deletions,
                total_changes=total_changes,
                code_files=code_files,
                code_file_count=len(code_files),
                code_changes=code_changes,
                strategy=strategy,
                should_skip=should_skip,
                skip_reason=skip_reason,
            )

            logger.info(
                f"PR分析完成: {pr_info['repo_full_name']}#{pr_info['pr_number']}, "
                f"文件数: {len(code_files)}, 变更行数: {code_changes}, "
                f"策略: {strategy}"
            )

            return analysis

        except Exception as e:
            logger.error(f"分析PR时出错: {e}", exc_info=True)
            raise

    def _should_skip_review(
        self, code_file_count: int, code_changes: int, total_files: int
    ) -> Tuple[bool, Optional[str]]:
        """判断是否应该跳过审查"""
        # 检查是否有代码文件
        if code_file_count == 0:
            return True, "没有代码文件变更"

        # 检查变更是否过小
        if code_changes == 0:
            return True, "没有代码变更"

        # 检查是否超过最大限制
        if code_file_count > settings.max_file_count:
            return (
                True,
                f"文件数超过限制 ({code_file_count} > {settings.max_file_count})",
            )

        if code_changes > settings.max_line_count:
            return (
                True,
                f"变更行数超过限制 ({code_changes} > {settings.max_line_count})",
            )

        return False, None

    def get_project_structure(self, repo: any, max_files: int = 500) -> List[str]:
        """获取项目的目录结构
        
        Args:
            repo: GitHub仓库对象
            max_files: 最大文件数限制
            
        Returns:
            目录结构列表
        """
        try:
            # 获取仓库的Git树
            tree = repo.get_git_tree(repo.default_branch, recursive=True)
            
            # 获取跳过路径配置
            skip_paths = strategy_config.get_file_filters().get("skip_paths", [])
            
            structure = []
            file_count = 0
            
            # 按路径排序并格式化
            for item in sorted(tree.tree, key=lambda x: x.path):
                if file_count >= max_files:
                    structure.append(f"... (还有 {len(tree.tree) - max_files} 个文件未显示)")
                    break
                
                # 检查是否应该跳过该路径
                should_skip = False
                for skip_path in skip_paths:
                    if item.path.startswith(skip_path.rstrip('/')):
                        should_skip = True
                        break
                
                if should_skip:
                    continue
                
                if item.type == 'tree':
                    structure.append(f"📁 {item.path}/")
                else:
                    structure.append(f"📄 {item.path}")
                    file_count += 1
            
            logger.info(f"获取项目结构完成，共 {min(len(tree.tree), max_files)} 个项目（已过滤skip_paths）")
            return structure
            
        except Exception as e:
            logger.error(f"获取项目结构失败: {e}", exc_info=True)
            return []

    def prepare_review_context(self, analysis: PRAnalysis, pr: any) -> Dict[str, any]:
        """准备审查上下文"""
        try:
            # 根据策略准备不同级别的上下文
            strategy_name = analysis.strategy
            strategy_info = strategy_config.get_strategy(strategy_name)
            
            # 获取仓库对象 - 使用 pr.base.repo 而不是 pr.repository
            repo = pr.base.repo

            # 获取项目结构
            project_structure = self.get_project_structure(repo)

            context = {
                "strategy": strategy_name,
                "strategy_name": strategy_info.get("name", strategy_name),
                "files": [],
                "total_changes": analysis.code_changes,
                "file_count": analysis.code_file_count,
                "project_structure": project_structure,
                "tools_available": [
                    "read_file: 查看任意文件的完整内容",
                    "list_directory: 列出目录中的文件"
                ]
            }

            # 对于小型PR，包含完整的patch
            if strategy_name in ["quick", "standard"]:
                for file_info in analysis.code_files:
                    file_context = {
                        "path": file_info.path,
                        "status": file_info.status,
                        "changes": file_info.changes,
                        "additions": file_info.additions,
                        "deletions": file_info.deletions,
                    }

                    # 包含patch（如果可用）
                    if file_info.patch:
                        # 限制patch大小
                        patch_lines = file_info.patch.split("\n")
                        if len(patch_lines) > 500:
                            file_context["patch"] = (
                                "\n".join(patch_lines[:500]) + "\n... (truncated)"
                            )
                        else:
                            file_context["patch"] = file_info.patch

                    context["files"].append(file_context)

            # 对于大型PR，只包含文件列表和摘要
            elif strategy_name == "deep":
                # 分批处理
                batch_config = strategy_config.get_batch_config()
                max_files_per_batch = batch_config.get("max_files_per_batch", 10)

                # 对文件按重要性排序（变更量大的优先）
                sorted_files = sorted(
                    analysis.code_files, key=lambda f: f.changes, reverse=True
                )

                for file_info in sorted_files[:max_files_per_batch]:
                    context["files"].append(
                        {
                            "path": file_info.path,
                            "status": file_info.status,
                            "changes": file_info.changes,
                            "patch": file_info.patch
                            if file_info.patch
                            and len(file_info.patch.split("\n")) < 300
                            else None,
                        }
                    )

                if len(analysis.code_files) > max_files_per_batch:
                    context["remaining_files"] = (
                        len(analysis.code_files) - max_files_per_batch
                    )

            # 对于超大PR，只包含概览
            elif strategy_name == "large":
                context["file_summary"] = [
                    {"path": f.path, "changes": f.changes, "status": f.status}
                    for f in analysis.code_files[:20]
                ]
                if len(analysis.code_files) > 20:
                    context["remaining_files"] = len(analysis.code_files) - 20

            return context

        except Exception as e:
            logger.error(f"准备审查上下文时出错: {e}", exc_info=True)
            raise
