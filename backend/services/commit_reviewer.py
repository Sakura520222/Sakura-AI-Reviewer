"""Commit级别审查服务"""

import asyncio
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from loguru import logger

from backend.services.pr_analyzer import CommitInfo
from backend.core.config import get_settings
from backend.services.ai_reviewer.reviewer import AIReviewer


@dataclass
class CommitReviewResult:
    """单个commit的审查结果"""

    commit_sha: str
    commit_position: int
    commit_message: str
    score: Optional[int] = None
    summary: str = ""
    issues: Optional[Dict[str, List]] = None
    inline_comments: List[Dict] = None

    def __post_init__(self):
        if self.issues is None:
            self.issues = {
                "critical": [],
                "major": [],
                "minor": [],
                "suggestions": [],
            }
        if self.inline_comments is None:
            self.inline_comments = []


@dataclass
class CommitReviewSummary:
    """Commit审查汇总结果"""

    is_incremental: bool = False  # 是否为增量审查
    total_commits: int = 0
    reviewed_commits: int = 0
    overall_score: Optional[int] = None
    summary: str = ""
    commit_reviews: List[CommitReviewResult] = None
    all_comments: List[Dict] = None
    all_inline_comments: List[Dict] = None

    def __post_init__(self):
        if self.commit_reviews is None:
            self.commit_reviews = []
        if self.all_comments is None:
            self.all_comments = []
        if self.all_inline_comments is None:
            self.all_inline_comments = []


class CommitReviewer:
    """Commit级别审查器"""

    def __init__(self, ai_reviewer: AIReviewer):
        self.ai_reviewer = ai_reviewer
        self.settings = get_settings()

    async def review_commits(
        self,
        commits: List[CommitInfo],
        context: Dict[str, Any],
        repo: Any,
        pr: Any,
        is_incremental: bool = False,
    ) -> CommitReviewSummary:
        """审查多个commits

        Args:
            commits: commit信息列表
            context: 审查上下文
            repo: GitHub仓库对象
            pr: GitHub PR对象
            is_incremental: 是否为增量审查

        Returns:
            Commit审查汇总结果
        """
        if not commits:
            logger.warning("没有commits需要审查")
            return CommitReviewSummary(is_incremental=is_incremental)

        logger.info(
            f"开始审查 {len(commits)} 个commits（增量审查: {is_incremental}）"
        )

        # 并发控制
        semaphore = asyncio.Semaphore(self.settings.commit_review_concurrency)

        async def review_one(commit: CommitInfo):
            async with semaphore:
                return await self._review_single_commit(
                    commit, context, repo, pr, commits
                )

        # 并行执行
        results = await asyncio.gather(
            *[review_one(c) for c in commits], return_exceptions=True
        )

        # 处理结果
        commit_reviews = []
        all_inline_comments = []
        valid_results = []

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Commit {i+1} 审查失败: {result}")
                continue

            valid_results.append(result)
            commit_reviews.append(result)
            all_inline_comments.extend(result.inline_comments)

        # 合并结果
        summary = self._format_commit_summary(
            valid_results, commits, is_incremental
        )

        # 计算平均分
        scores = [
            r.score for r in valid_results if r.score is not None
        ]
        overall_score = int(sum(scores) / len(scores)) if scores else None

        review_summary = CommitReviewSummary(
            is_incremental=is_incremental,
            total_commits=len(commits),
            reviewed_commits=len(valid_results),
            overall_score=overall_score,
            summary=summary,
            commit_reviews=commit_reviews,
            all_comments=[],  # commit级别暂不需要整体评论
            all_inline_comments=all_inline_comments,
        )

        logger.info(
            f"Commit审查完成: {len(valid_results)}/{len(commits)}个, "
            f"平均分: {overall_score}"
        )

        return review_summary

    async def _review_single_commit(
        self,
        commit: CommitInfo,
        context: Dict[str, Any],
        repo: Any,
        pr: Any,
        all_commits: List[CommitInfo],
    ) -> CommitReviewResult:
        """审查单个commit

        Args:
            commit: commit信息
            context: 审查上下文
            repo: GitHub仓库对象
            pr: GitHub PR对象
            all_commits: 所有commit列表（用于上下文）

        Returns:
            Commit审查结果
        """
        logger.info(
            f"审查commit {commit.position}/{len(all_commits)}: "
            f"{commit.sha[:7]} - {commit.message[:50]}"
        )

        try:
            # 构建commit专属上下文
            commit_context = self._build_commit_context(
                commit, context, all_commits
            )

            # 使用AI审查（复用现有接口）
            strategy = context.get("strategy", "standard")
            result = await self.ai_reviewer.review_pr_with_tools(
                commit_context, strategy, repo, pr
            )

            # 解析结果
            score = self._extract_score(result.get("summary", ""))
            issues = result.get("issues", {})
            inline_comments = result.get("inline_comments", [])

            return CommitReviewResult(
                commit_sha=commit.sha,
                commit_position=commit.position,
                commit_message=commit.message,
                score=score,
                summary=result.get("summary", ""),
                issues=issues,
                inline_comments=inline_comments,
            )

        except Exception as e:
            logger.error(f"审查commit {commit.sha[:7]} 失败: {e}", exc_info=True)
            # 返回一个默认结果
            return CommitReviewResult(
                commit_sha=commit.sha,
                commit_position=commit.position,
                commit_message=commit.message,
                score=None,
                summary=f"审查失败: {str(e)}",
            )

    def _build_commit_context(
        self,
        commit: CommitInfo,
        base_context: Dict[str, Any],
        all_commits: List[CommitInfo],
    ) -> Dict[str, Any]:
        """构建commit审查的上下文

        Args:
            commit: 当前commit信息
            base_context: 基础上下文
            all_commits: 所有commit列表

        Returns:
            commit专属上下文
        """
        # 构建文件变更描述
        files_diff = self._format_commit_files(commit)

        context = {
            **base_context,
            "commit": {
                "sha": commit.sha,
                "message": commit.message,
                "author": commit.author,
                "position": commit.position,
                "total": len(all_commits),
            },
            "files": [
                {
                    "path": f.path,
                    "status": f.status,
                    "changes": f.changes,
                    "additions": f.additions,
                    "deletions": f.deletions,
                    "patch": f.patch,
                }
                for f in commit.files
            ],
            "files_diff": files_diff,
            "is_commit_review": True,  # 标记为commit审查
        }

        return context

    def _format_commit_files(self, commit: CommitInfo) -> str:
        """格式化commit的文件变更

        Args:
            commit: commit信息

        Returns:
            格式化的文件变更描述
        """
        lines = [f"### Commit: {commit.sha[:7]} - {commit.message}"]
        lines.append(f"**作者**: {commit.author}")
        lines.append(f"**变更**: {commit.additions}+ / {commit.deletions}-")
        lines.append("")

        if not commit.files:
            lines.append("*无文件变更*")
            return "\n".join(lines)

        lines.append("#### 文件变更:")
        for file in commit.files:
            status_icon = {
                "added": "➕",
                "modified": "✏️",
                "deleted": "❌",
                "renamed": "🔄",
            }.get(file.status, "📄")

            lines.append(
                f"- {status_icon} `{file.path}` "
                f"({file.status}: +{file.additions} -{file.deletions})"
            )

            # 如果有patch且不太大，包含部分内容
            if file.patch and len(file.patch) < 3000:
                lines.append("```diff")
                lines.append(file.patch[:500] + ("..." if len(file.patch) > 500 else ""))
                lines.append("```")
            elif file.patch:
                lines.append(f"  *(Patch过大: {len(file.patch)} 字符)*")

            lines.append("")

        return "\n".join(lines)

    def _extract_score(self, summary: str) -> Optional[int]:
        """从AI返回的summary中提取评分

        Args:
            summary: AI返回的摘要

        Returns:
            评分（1-10），未找到则返回None
        """
        import re

        # 尝试匹配 "评分：X/10" 或 "代码质量评分：X/10" 或 "X/10"
        patterns = [
            r"评分[：:]\s*(\d+)/10",
            r"代码质量评分[：:]\s*(\d+)/10",
            r"\*\*(\d+)/10\*\*",
            r"(\d+)/10",
        ]

        for pattern in patterns:
            match = re.search(pattern, summary)
            if match:
                try:
                    score = int(match.group(1))
                    if 1 <= score <= 10:
                        return score
                except (ValueError, IndexError):
                    continue

        return None

    def _format_commit_summary(
        self,
        results: List[CommitReviewResult],
        commits: List[CommitInfo],
        is_incremental: bool,
    ) -> str:
        """格式化commit审查汇总

        Args:
            results: 审查结果列表
            commits: commit列表
            is_incremental: 是否为增量审查

        Returns:
            格式化的汇总文本
        """
        if is_incremental:
            title = "## 🔍 增量审查 - 新增Commits"
        else:
            title = "## Commit级别审查"

        lines = [
            f"{title} ({len(results)}个)",
            "",
            "### Commit审查概览",
            "",
            "| Commit | 消息 | 评分 | 变更 | 问题 |",
            "|--------|------|------|------|------|",
        ]

        for result, commit in zip(results, commits):
            sha_short = commit.sha[:7]
            message = commit.message[:40] + (
                "..." if len(commit.message) > 40 else ""
            )
            score = f"{result.score}/10" if result.score else "N/A"
            changes = f"+{commit.additions}/-{commit.deletions}"

            # 统计问题数
            critical = len(result.issues.get("critical", []))
            major = len(result.issues.get("major", []))
            minor = len(result.issues.get("minor", []))

            if critical > 0:
                issues = f"{critical} 🔴"
            elif major > 0:
                issues = f"{major} 🟡"
            elif minor > 0:
                issues = f"{minor} 💡"
            else:
                issues = "-"

            lines.append(
                f"| `{sha_short}` | {message} | {score} | {changes} | {issues} |"
            )

        lines.append("")

        # 添加每个commit的详细审查（可折叠）
        for result, commit in zip(results, commits):
            sha_short = commit.sha[:7]
            score_display = f"{result.score}/10" if result.score else "N/A"

            lines.append("<details>")
            lines.append(f"<summary>📝 Commit {sha_short} 详细审查</summary>")
            lines.append("")
            lines.append(f"### Commit: {sha_short} - {commit.message}")
            lines.append(f"- **评分**: {score_display}")
            lines.append(f"- **作者**: {commit.author}")
            lines.append(f"- **变更**: {commit.additions}+ / {commit.deletions}-")
            lines.append("")

            # 添加问题
            if result.issues.get("critical"):
                lines.append("#### 🔴 严重问题")
                for i, issue in enumerate(result.issues["critical"], 1):
                    lines.append(f"{i}. {issue}")
                lines.append("")

            if result.issues.get("major"):
                lines.append("#### 🟡 重要问题")
                for i, issue in enumerate(result.issues["major"], 1):
                    lines.append(f"{i}. {issue}")
                lines.append("")

            if result.issues.get("minor"):
                lines.append("#### 💡 改进建议")
                for i, issue in enumerate(result.issues["minor"], 1):
                    lines.append(f"{i}. {issue}")
                lines.append("")

            if result.issues.get("suggestions"):
                lines.append("#### 📝 优化建议")
                for i, issue in enumerate(result.issues["suggestions"], 1):
                    lines.append(f"{i}. {issue}")
                lines.append("")

            # 如果没有问题
            if not any(result.issues.values()):
                lines.append("✅ **未发现问题**")
                lines.append("")

            lines.append("</details>")
            lines.append("")

        # 如果是增量审查，添加说明
        if is_incremental:
            lines.insert(
                3,
                "**说明**: 本次仅审查新增的commits，之前的commits已在上次审查中完成。",
            )
            lines.insert(4, "")

        return "\n".join(lines)

    def format_incremental_review_body(
        self, summary: CommitReviewSummary, pr_number: int
    ) -> str:
        """格式化增量审查的Review评论体

        Args:
            summary: 审查汇总结果
            pr_number: PR编号

        Returns:
            格式化的Review评论
        """
        score_display = f"{summary.overall_score}/10" if summary.overall_score else "N/A"

        body = f"""# 🔍 增量审查报告 - PR #{pr_number}

{summary.summary}

---

## 📊 整体评分: {score_display}

**审查范围**: {summary.reviewed_commits} 个新增commits

---

*🌸 由 Sakura AI 提供增量审查支持*
"""

        return body
