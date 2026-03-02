"""GitHub App集成模块"""

import hmac
import hashlib
from typing import Optional, Dict, Any
from github import Github, GithubIntegration
from loguru import logger
from backend.core.config import get_settings

settings = get_settings()


class GitHubAppClient:
    """GitHub App客户端（线程安全单例模式）"""

    _instance = None
    _lock = None
    _initialized = False

    def __new__(cls):
        """确保只有一个实例"""
        if cls._instance is None:
            import threading

            cls._lock = threading.Lock()
            with cls._lock:
                # 双重检查
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """初始化（只执行一次）"""
        if not self._initialized:
            self.integration = None
            self._app_client = None
            self._lock = None
            self._init_integration()
            self.__class__._initialized = True
            logger.info("GitHubAppClient单例初始化完成")

    def _init_integration(self):
        """初始化GitHub Integration"""
        try:
            self.integration = self._create_integration()
        except Exception as e:
            logger.error(f"GitHub App客户端初始化失败: {e}", exc_info=True)
            raise

    def _create_integration(self) -> GithubIntegration:
        """创建GitHub Integration实例"""
        try:
            # 获取配置
            app_id = str(settings.github_app_id)  # 确保是字符串
            private_key = settings.github_private_key

            logger.info(
                f"开始创建GitHub Integration, App ID: {app_id} (类型: {type(app_id).__name__})"
            )

            # 清理私钥格式：先处理转义换行，再去除首尾所有空白字符
            private_key = private_key.replace("\\n", "\n").strip()
            logger.debug(f"私钥处理完成，长度: {len(private_key)} 字符")

            # 验证私钥标记（使用 in 关键字比 endswith 更稳健）
            if "-----BEGIN" not in private_key:
                logger.error("私钥格式错误：缺少 BEGIN 标记")
                raise ValueError("私钥格式无效：缺少BEGIN标记")

            if "-----END" not in private_key:
                logger.error("私钥格式错误：缺少 END 标记")
                logger.debug(f"私钥结尾检查: '{private_key[-50:]}'")
                raise ValueError("私钥格式无效：缺少END标记")

            # 输出调试信息（脱敏）
            logger.debug(f"私钥预览: {private_key[:50]}...{private_key[-50:]}")

            # 创建 GithubIntegration 实例（app_id保持为字符串）
            logger.info("正在创建GithubIntegration实例...")
            integration = GithubIntegration(
                integration_id=app_id,  # 传入字符串，不转换为int
                private_key=private_key,
            )
            logger.info(f"✓ GitHub Integration创建成功, App ID: {app_id}")
            return integration

        except ValueError as e:
            logger.error(f"GitHub App配置验证失败: {e}")
            raise
        except Exception as e:
            logger.error(f"GitHub App初始化失败: {e}", exc_info=True)
            raise

    def get_app_client(self) -> Github:
        """获取App级别的GitHub客户端"""
        if self._app_client is None:
            # 获取App的访问令牌
            token = self.integration.get_access_token(settings.github_app_id)
            self._app_client = Github(login_or_token=token)
        return self._app_client

    def get_installation_client(
        self, repo_owner: str, repo_name: str
    ) -> Optional[Github]:
        """获取安装级别的GitHub客户端（用于访问特定仓库）"""
        try:
            # 获取安装ID
            installation = self.integration.get_installation(
                owner=repo_owner, repo=repo_name
            )

            # 获取安装访问令牌（新版 PyGithub API）
            auth_token = self.integration.get_access_token(installation.id)
            token = auth_token.token

            # 创建客户端
            client = Github(login_or_token=token)
            logger.info(f"成功获取仓库 {repo_owner}/{repo_name} 的访问令牌")
            return client
        except Exception as e:
            logger.error(f"获取仓库 {repo_owner}/{repo_name} 的安装客户端失败: {e}")
            return None

    def get_repo_client(self, repo_owner: str, repo_name: str) -> Optional[Github]:
        """根据仓库信息获取GitHub客户端（带重试机制）"""
        max_retries = 2
        last_error = None

        for attempt in range(max_retries):
            try:
                logger.debug(
                    f"尝试获取仓库客户端 [{attempt + 1}/{max_retries}]: {repo_owner}/{repo_name}"
                )

                # 检查integration是否存在
                if self.integration is None:
                    logger.warning("Integration为空，尝试重新创建...")
                    self._init_integration()

                # 获取安装信息
                logger.debug("正在获取installation信息...")
                installation = self.integration.get_installation(
                    owner=repo_owner, repo=repo_name
                )
                logger.debug(f"获取installation成功，ID: {installation.id}")

                # 获取访问令牌（新版 PyGithub API）
                logger.debug("正在生成访问令牌...")
                auth_token = self.integration.get_access_token(installation.id)
                token = auth_token.token
                logger.debug(f"访问令牌生成成功，前缀: {token[:10]}...")

                # 创建客户端
                client = Github(login_or_token=token)
                logger.info(f"✓ 成功获取仓库 {repo_owner}/{repo_name} 的访问令牌")
                return client

            except Exception as e:
                last_error = e
                logger.error(
                    f"获取仓库客户端失败 [尝试 {attempt + 1}/{max_retries}]: {e}",
                    exc_info=True,
                )

                # 如果是最后一次尝试失败，重新创建integration
                if attempt == 0:
                    logger.warning("第一次尝试失败，重新创建Integration...")
                    try:
                        self._init_integration()
                    except Exception as init_error:
                        logger.error(f"重新创建Integration失败: {init_error}")

        # 所有尝试都失败
        logger.error(
            f"获取仓库 {repo_owner}/{repo_name} 的客户端失败，已重试 {max_retries} 次"
        )
        logger.error(f"最后错误: {last_error}")
        return None


def verify_webhook_signature(payload: bytes, signature: str) -> bool:
    """验证Webhook签名"""
    try:
        # GitHub签名格式: sha256=<hash>
        if not signature.startswith("sha256="):
            logger.warning(f"无效的签名格式: {signature}")
            return False

        # 提取签名哈希
        hash_signature = signature.split("=")[1]

        # 计算预期签名
        secret = settings.github_webhook_secret.encode("utf-8")
        expected_signature = hmac.new(secret, payload, hashlib.sha256).hexdigest()

        # 使用安全的字符串比较
        is_valid = hmac.compare_digest(hash_signature, expected_signature)

        if not is_valid:
            logger.warning("Webhook签名验证失败")

        return is_valid
    except Exception as e:
        logger.error(f"验证Webhook签名时出错: {e}")
        return False


def extract_pr_info_from_webhook(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """从Webhook payload中提取PR信息"""
    try:
        # 检查是否为PR事件
        action = payload.get("action")
        pull_request = payload.get("pull_request")
        repository = payload.get("repository")
        installation = payload.get("installation")

        if not pull_request or not repository or not installation:
            logger.warning("Webhook payload中缺少必要字段")
            return None

        # 提取信息
        pr_info = {
            "action": action,
            "pr_id": pull_request["id"],
            "pr_number": pull_request["number"],
            "repo_owner": repository["owner"]["login"],
            "repo_name": repository["name"],
            "repo_full_name": repository["full_name"],
            "installation_id": installation["id"],
            "author": pull_request["user"]["login"],
            "title": pull_request["title"],
            "branch": pull_request["head"]["ref"],
            "base_branch": pull_request["base"]["ref"],
            "diff_url": pull_request["diff_url"],
            "patch_url": pull_request["patch_url"],
            "html_url": pull_request["html_url"],
            "state": pull_request["state"],
            "draft": pull_request.get("draft", False),
            "merged": pull_request.get("merged", False),
        }

        logger.info(
            f"成功提取PR信息: {pr_info['repo_full_name']}#{pr_info['pr_number']}"
        )
        return pr_info

    except Exception as e:
        logger.error(f"提取PR信息时出错: {e}")
        return None
