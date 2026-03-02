"""评论服务 - 负责将审查结果发布到GitHub PR"""

from typing import Dict, Any
from loguru import logger

from backend.core.config import get_strategy_config

strategy_config = get_strategy_config()


class CommentService:
    """评论服务"""

    def __init__(self):
        pass

    async def create_placeholder_comment(self, pr: Any, strategy: str) -> Any:
        """创建占位评论（使用 Issue Comment）
        
        使用 Issue Comment 而不是 PR Review，因为 COMMENT 类型的 review 无法被 dismiss。

        Args:
            pr: GitHub PR 对象
            strategy: 审查策略名称

        Returns:
            GitHub Issue Comment 对象，用于后续删除
        """
        try:
            # 获取策略名称
            strategy_info = strategy_config.get_strategy(strategy)
            strategy_name = strategy_info.get("name", "代码审查")

            # 构建占位消息
            placeholder_body = f"""# 🔄 正在审查中...

**Sakura AI** 正在使用 **{strategy_name}** 策略分析此 PR，请稍候...

---

*⏳ Sakura 正在思考中，这可能需要几十秒到几分钟，取决于代码量和复杂度*
"""

            # 使用 Issue Comment（可编辑、可删除）
            comment = pr.create_issue_comment(placeholder_body)

            logger.info(
                f"✓ 已创建占位评论到PR: {pr.base.repo.full_name}#{pr.number} "
                f"(Comment ID: {comment.id})"
            )

            return comment

        except Exception as e:
            logger.error(f"创建占位评论时出错: {e}", exc_info=True)
            raise

    async def update_review(
        self, comment: Any, review_result: Dict[str, Any], strategy: str, pr: Any = None
    ):
        """更新已有的审查评论
        
        使用删除占位 comment + 创建正式 PR Review 的策略。
        
        Args:
            comment: GitHub Issue Comment 对象（占位评论）
            review_result: AI 审查结果
            strategy: 审查策略名称
            pr: GitHub PR 对象
        """
        try:
            # 构建完整的评论内容
            review_body = self._format_comment(review_result, strategy)

            if pr:
                try:
                    # 1. 删除占位 comment
                    comment.delete()
                    logger.info(f"✓ 已删除占位评论 (Comment ID: {comment.id})")
                    
                    # 2. 创建正式的 PR Review
                    review = pr.create_review(
                        body=review_body,
                        event="COMMENT",
                        comments=[]
                    )
                    logger.info(f"✓ 已创建正式审查评论 (Review ID: {review.id})")
                    
                except Exception as e:
                    logger.error("删除评论并创建 review 失败: {}", str(e), exc_info=True)
                    # 降级方案：创建新的普通评论
                    pr.create_issue_comment(
                        "⚠️ 审查评论更新失败，已降级为普通评论\n\n" + review_body
                    )
                    logger.info("✓ 已降级为普通评论")
            else:
                logger.warning("无法更新评论：没有 PR 对象")

        except Exception as e:
            logger.error("更新评论时出错: {}", str(e), exc_info=True)
            raise

    async def update_review_with_error(self, comment: Any, error_message: str, pr: Any = None):
        """将占位评论更新为错误消息
        
        使用删除占位 comment + 创建错误评论的策略。

        Args:
            comment: GitHub Issue Comment 对象（占位评论）
            error_message: 错误信息
            pr: GitHub PR 对象
        """
        try:
            error_body = f"""# ❌ 审查失败

抱歉，Sakura 在审查过程中出现错误：

```
{error_message}
```

请检查系统日志或联系管理员。

---

*此评论由 Sakura AI Reviewer 自动生成*
"""

            if pr:
                try:
                    # 1. 删除占位 comment
                    comment.delete()
                    logger.info(f"✓ 已删除占位评论 (Comment ID: {comment.id})")
                    
                    # 2. 创建错误评论
                    pr.create_issue_comment(error_body)
                    logger.info("✓ 已创建错误评论")
                    
                except Exception as e:
                    logger.error("删除评论并创建错误评论失败: {}", str(e), exc_info=True)
                    # 降级方案：直接创建新的错误评论
                    pr.create_issue_comment(error_body)
                    logger.info("✓ 已降级为直接创建错误评论")
            else:
                logger.warning("无法更新错误评论：没有 PR 对象")

        except Exception as e:
            logger.error("更新错误消息时出错: {}", str(e), exc_info=True)
            # 如果更新失败，尝试记录日志但不中断流程

    async def post_review_comment(
        self, pr: Any, review_result: Dict[str, Any], strategy: str
    ):
        """发布审查评论到PR（使用create_review一次性发布）

        注意：此方法保留用于向后兼容，新代码应使用 create_placeholder_review + update_review
        """
        try:
            # 构建评论内容
            comment_body = self._format_comment(review_result, strategy)

            # 使用 create_review 一次性发布所有评论
            # 这样可以避免触发 GitHub API 频率限制
            pr.create_review(
                body=comment_body,
                event="COMMENT",  # COMMENT, APPROVE, REQUEST_CHANGES
                comments=[],  # 整体评论，不需要行内评论
            )

            logger.info(f"✓ 成功发布审查评论到PR: {pr.base.repo.full_name}#{pr.number}")

        except Exception as e:
            logger.error(f"发布评论时出错: {e}", exc_info=True)
            raise

    def _format_comment(self, review_result: Dict[str, Any], strategy: str) -> str:
        """格式化评论内容"""
        lines = []

        # 添加标题
        strategy_info = strategy_config.get_strategy(strategy)
        strategy_name = strategy_info.get("name", "代码审查")
        lines.append(f"# 🌸 Sakura AI 审查报告 - {strategy_name}\n")

        # 添加评分（如果有）
        overall_score = review_result.get("overall_score")
        if overall_score:
            lines.append(f"## 📊 整体评分: {overall_score}/10\n")

        # 添加摘要
        summary = review_result.get("summary", "")
        if summary:
            lines.append("## 📝 审查摘要\n")
            lines.append(summary)
            lines.append("")

        # 添加分类问题
        issues = review_result.get("issues", {})

        # 严重问题
        critical_issues = issues.get("critical", [])
        if critical_issues:
            lines.append("## 🔴 严重问题（必须修复）\n")
            for issue in critical_issues:
                lines.append(f"- {issue}")
            lines.append("")

        # 重要问题
        major_issues = issues.get("major", [])
        if major_issues:
            lines.append("## 🟡 重要建议（推荐改进）\n")
            for issue in major_issues:
                lines.append(f"- {issue}")
            lines.append("")

        # 建议
        suggestions = issues.get("suggestions", [])
        if suggestions:
            lines.append("## 💡 优化建议\n")
            for suggestion in suggestions:
                lines.append(f"- {suggestion}")
            lines.append("")

        # 添加页脚
        lines.append("\n---\n")
        lines.append("*此评论由 Sakura AI Reviewer 自动生成*")

        return "\n".join(lines)

    async def create_review_comment(self, pr: Any, body: str, event: str = "COMMENT"):
        """创建PR审查评论"""
        try:
            review = pr.create_review(body=body, event=event, comments=[])
            return review
        except Exception as e:
            logger.error(f"创建审查评论时出错: {e}")
            raise

    async def create_inline_comment(
        self, pr: Any, file_path: str, line_number: int, body: str
    ):
        """创建行内评论"""
        try:
            # 获取commit
            commit = pr.head.repo.get_commit(pr.head.sha)

            # 创建行内评论
            comment = pr.create_review_comment(
                body=body, path=file_path, line=line_number, commit_id=commit.sha
            )
            return comment
        except Exception as e:
            logger.error(f"创建行内评论时出错: {e}")
            raise

    def format_file_review(
        self, file_path: str, review_text: str, line_number: int = None
    ) -> str:
        """格式化文件审查评论"""
        comment = f"## 📄 {file_path}\n\n"
        if line_number:
            comment += f"**行号**: {line_number}\n\n"
        comment += review_text
        return comment
