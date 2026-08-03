import httpx
import logging
from typing import Dict, Any, Optional
from app.config import Config

logger = logging.getLogger(__name__)

class GitHubTool:
    """Provides tools for interacting with the GitHub API (collaborations, SSH keys, repository management)."""
    
    def __init__(self):
        self.token = Config.GITHUB_TOKEN
        self.default_owner = Config.GITHUB_USERNAME
        
    def _get_headers(self) -> dict:
        """Construct standard authorization headers for GitHub API."""
        if not self.token:
            raise ValueError("GITHUB_TOKEN отсутствует в настройках окружения.")
        return {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }

    async def invite_collaborator(self, repo_name: str, username: str, permission: str = "push") -> Dict[str, Any]:
        """Invite a collaborator to a GitHub repository."""
        logger.info(f"Inviting collaborator {username} to {repo_name} with {permission} permission.")
        try:
            # Smart parsing in case repo_name is in format "owner/repo"
            if "/" in repo_name:
                owner, repo = repo_name.split("/", 1)
            else:
                owner = self.default_owner
                repo = repo_name
                
            if not owner:
                return {"success": False, "error": "Не удалось определить владельца репозитория (GITHUB_USERNAME)."}

            url = f"https://api.github.com/repos/{owner}/{repo}/collaborators/{username}"
            headers = self._get_headers()
            payload = {"permission": permission}

            async with httpx.AsyncClient() as client:
                response = await client.put(url, headers=headers, json=payload, timeout=15.0)
                
                if response.status_code in [201, 204]:
                    # 201 Created (invite sent), 204 No Content (already collaborator)
                    status_msg = "приглашение успешно отправлено!" if response.status_code == 201 else "пользователь уже является коллаборатором."
                    return {
                        "success": True,
                        "status_code": response.status_code,
                        "message": f"Успешно! Для пользователя `{username}` {status_msg}"
                    }
                else:
                    return {
                        "success": False,
                        "status_code": response.status_code,
                        "error": response.text
                    }
        except Exception as e:
            logger.error(f"Error inviting collaborator: {e}")
            return {"success": False, "error": str(e)}

    async def add_ssh_key_to_github(self, title: str, key_content: str) -> Dict[str, Any]:
        """Add a public SSH key to the authenticated user's GitHub account."""
        logger.info(f"Adding SSH key to GitHub: {title}")
        try:
            url = "https://api.github.com/user/keys"
            headers = self._get_headers()
            payload = {
                "title": title,
                "key": key_content
            }

            async with httpx.AsyncClient() as client:
                response = await client.post(url, headers=headers, json=payload, timeout=15.0)
                
                if response.status_code == 201:
                    data = response.json()
                    return {
                        "success": True,
                        "key_id": data.get("id"),
                        "message": f"SSH-ключ `{title}` успешно добавлен в твой аккаунт на GitHub! 🎉"
                    }
                else:
                    return {
                        "success": False,
                        "status_code": response.status_code,
                        "error": response.text
                    }
        except Exception as e:
            logger.error(f"Error adding SSH key to GitHub: {e}")
            return {"success": False, "error": str(e)}

    async def create_github_repo(self, repo_name: str, private: bool = True) -> Dict[str, Any]:
        """Create a new remote GitHub repository under the user's account."""
        logger.info(f"Creating GitHub repository: {repo_name} (private={private})")
        try:
            url = "https://api.github.com/user/repos"
            headers = self._get_headers()
            payload = {
                "name": repo_name,
                "private": private,
                "auto_init": False
            }

            async with httpx.AsyncClient() as client:
                response = await client.post(url, headers=headers, json=payload, timeout=15.0)
                
                if response.status_code == 201:
                    data = response.json()
                    return {
                        "success": True,
                        "repo_url": data.get("clone_url"),
                        "html_url": data.get("html_url"),
                        "message": f"Репозиторий `{repo_name}` успешно создан на GitHub! Ссылка: {data.get('html_url')}"
                    }
                else:
                    return {
                        "success": False,
                        "status_code": response.status_code,
                        "error": response.text
                    }
        except Exception as e:
            logger.error(f"Error creating GitHub repository: {e}")
            return {"success": False, "error": str(e)}
