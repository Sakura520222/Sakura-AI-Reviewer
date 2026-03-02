"""审查任务Worker"""

import asyncio
from typing import Dict, Any, Optional
from datetime import datetime
from loguru import logger
import uuid

from backend.core.config import get_settings
from backend.core.github_app import GitHubAppClient
from backend.services.pr_analyzer import PRAnalyzer, PRAnalysis
from backend.services.ai_reviewer import AIReviewer
from backend.services.comment_service import CommentService
from backend.models.database import (
    PRReview,
    PRStatus,
    ReviewStrategy,
    ReviewComment,
    CommentSeverity,
    CommentType,
)

settings = get_settings()


def get_async_session():
    """获取异步会话工厂（动态导入）"""
    from backend.models.database import async_session

    if async_session is None:
        raise RuntimeError("数据库未初始化，请确保 init_db() 已被调用")
    return async_session


class ReviewWorker:
    """审查任务Worker"""

    def __init__(self):
        self.github_app = GitHubAppClient()
        self.analyzer = PRAnalyzer()
        self.ai_reviewer = AIReviewer()
        self.comment_service = CommentService()

    async def process_review_task(self, pr_info: Dict[str, Any]) -> str:
        """处理审查任务"""
        task_id = str(uuid.uuid4())
        review_obj = None  # 用于保存 GitHub Review 对象

        try:
            logger.info(
                f"[{task_id}] 开始处理审查任务: {pr_info['repo_full_name']}#{pr_info['pr_number']}"
            )

            # 1. 分析PR
            analysis = await self.analyzer.analyze_pr(pr_info)

            # 2. 检查是否应该跳过
            if analysis.should_skip:
                logger.info(f"[{task_id}] 跳过审查: {analysis.skip_reason}")
                await self._save_skip_record(analysis, pr_info)
                return task_id

            # 3. 创建数据库记录
            review_id = await self._create_review_record(analysis, pr_info, task_id)

            # 4. 获取PR对象用于后续操作
            client = self.github_app.get_repo_client(
                pr_info["repo_owner"], pr_info["repo_name"]
            )
            repo = client.get_repo(pr_info["repo_full_name"])
            pr = repo.get_pull(pr_info["pr_number"])

            # 5. 【第一阶段】创建占位评论
            logger.info(f"[{task_id}] 创建占位评论...")
            review_obj = await self.comment_service.create_placeholder_comment(
                pr, analysis.strategy
            )

            # 6. 准备审查上下文
            context = self.analyzer.prepare_review_context(analysis, pr)

            # 7. 执行AI审查（使用增强的工具支持）
            await self._update_review_status(review_id, PRStatus.REVIEWING)

            # 根据配置决定是否使用AI工具
            enable_tools = settings.enable_ai_tools if hasattr(settings, 'enable_ai_tools') else True
            
            if enable_tools:
                logger.info(f"[{task_id}] 使用AI工具增强模式进行审查")
                review_result = await self.ai_reviewer.review_pr_with_tools(
                    context, analysis.strategy, repo, pr
                )
            else:
                logger.info(f"[{task_id}] 使用标准模式进行审查")
                review_result = await self.ai_reviewer.review_pr(context, analysis.strategy)

            # 8. 保存审查结果
            await self._save_review_results(review_id, review_result, analysis)

            # 9. 【第二阶段】更新评论为完整内容
            if review_obj:
                logger.info(f"[{task_id}] 更新评论为完整内容...")
                await self.comment_service.update_review(
                    review_obj, review_result, analysis.strategy, pr
                )

            # 10. 更新状态为完成
            await self._update_review_status(
                review_id,
                PRStatus.COMPLETED,
                overall_score=review_result.get("overall_score"),
            )

            logger.info(
                f"[{task_id}] 审查任务完成: {pr_info['repo_full_name']}#{pr_info['pr_number']}"
            )
            return task_id

        except Exception as e:
            logger.error(f"[{task_id}] 处理审查任务时出错: {e}", exc_info=True)

            # 【错误处理】更新占位评论为错误消息
            if review_obj:
                try:
                    await self.comment_service.update_review_with_error(
                        review_obj, str(e), pr
                    )
                    logger.info(f"[{task_id}] 已更新占位评论为错误状态")
                except Exception as update_error:
                    logger.error(f"[{task_id}] 更新错误消息失败: {update_error}")

            # 保存错误信息到数据库
            try:
                await self._save_error_record(pr_info, str(e), task_id)
            except Exception as save_error:
                logger.error(f"保存错误记录失败: {save_error}")
            raise

    async def _create_review_record(
        self, analysis: PRAnalysis, pr_info: Dict[str, Any], task_id: str
    ) -> int:
        """创建审查记录"""
        AsyncSession = get_async_session()
        async with AsyncSession() as session:
            record = PRReview(
                pr_id=analysis.pr_id,
                repo_name=pr_info["repo_name"],
                repo_owner=pr_info["repo_owner"],
                author=pr_info["author"],
                title=pr_info["title"],
                branch=pr_info["branch"],
                file_count=analysis.total_files,
                line_count=analysis.total_changes,
                code_file_count=analysis.code_file_count,
                strategy=ReviewStrategy(analysis.strategy),
                status=PRStatus.PENDING,
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
            logger.info(f"[{task_id}] 创建审查记录: {record.id}")
            return record.id

    async def _update_review_status(
        self, review_id: int, status: PRStatus, overall_score: Optional[int] = None
    ):
        """更新审查状态"""
        AsyncSession = get_async_session()
        async with AsyncSession() as session:
            record = await session.get(PRReview, review_id)
            if record:
                record.status = status
                if status == PRStatus.COMPLETED:
                    record.completed_at = datetime.utcnow()
                if overall_score is not None:
                    record.overall_score = overall_score
                await session.commit()

    async def _save_review_results(
        self, review_id: int, review_result: Dict[str, Any], analysis: PRAnalysis
    ):
        """保存审查结果"""
        AsyncSession = get_async_session()
        async with AsyncSession() as session:
            # 更新摘要
            record = await session.get(PRReview, review_id)
            if record:
                record.review_summary = review_result.get("summary", "")

            # 保存评论
            comments = review_result.get("comments", [])
            for comment_data in comments:
                comment = ReviewComment(
                    review_id=review_id,
                    file_path=None,  # 整体评论没有文件路径
                    line_number=None,
                    comment_type=CommentType.OVERALL,
                    severity=CommentSeverity(
                        comment_data.get("severity", "suggestion")
                    ),
                    content=comment_data["content"],
                )
                session.add(comment)

            await session.commit()
            logger.info(f"保存了 {len(comments)} 条评论")

    async def _save_skip_record(self, analysis: PRAnalysis, pr_info: Dict[str, Any]):
        """保存跳过记录"""
        AsyncSession = get_async_session()
        async with AsyncSession() as session:
            record = PRReview(
                pr_id=analysis.pr_id,
                repo_name=pr_info["repo_name"],
                repo_owner=pr_info["repo_owner"],
                author=pr_info["author"],
                title=pr_info["title"],
                branch=pr_info["branch"],
                file_count=analysis.total_files,
                line_count=analysis.total_changes,
                code_file_count=analysis.code_file_count,
                strategy=ReviewStrategy.SKIP,
                status=PRStatus.COMPLETED,
                review_summary=f"跳过审查: {analysis.skip_reason}",
            )
            session.add(record)
            await session.commit()

    async def _save_error_record(
        self, pr_info: Dict[str, Any], error_message: str, task_id: str
    ):
        """保存错误记录"""
        AsyncSession = get_async_session()
        async with AsyncSession() as session:
            record = PRReview(
                pr_id=pr_info["pr_id"],
                repo_name=pr_info["repo_name"],
                repo_owner=pr_info["repo_owner"],
                author=pr_info["author"],
                title=pr_info["title"],
                branch=pr_info["branch"],
                file_count=0,
                line_count=0,
                code_file_count=0,
                strategy=ReviewStrategy.STANDARD,
                status=PRStatus.FAILED,
                error_message=error_message,
            )
            session.add(record)
            await session.commit()
            logger.info(f"[{task_id}] 保存错误记录")


# 全局Worker实例
_worker_instance: Optional[ReviewWorker] = None


def get_worker() -> ReviewWorker:
    """获取Worker实例"""
    global _worker_instance
    if _worker_instance is None:
        _worker_instance = ReviewWorker()
    return _worker_instance


async def submit_review_task(pr_info: Dict[str, Any]) -> str:
    """提交审查任务（从Webhook调用）"""
    worker = get_worker()

    # 在生产环境中，这里应该提交到Celery队列
    # 为了简化，我们直接异步执行
    asyncio.create_task(worker.process_review_task(pr_info))

    # 返回任务ID（简化版，实际应该在提交到队列后返回）
    return str(uuid.uuid4())


async def process_review_task_sync(pr_info: Dict[str, Any]) -> str:
    """同步处理审查任务（用于Celery Worker）"""
    worker = get_worker()
    return await worker.process_review_task(pr_info)
