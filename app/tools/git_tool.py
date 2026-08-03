import os
import shutil
import logging
from git import Repo, Actor
from app.config import Config

logger = logging.getLogger(__name__)

class GitManager:
    """Manages Git repositories for user projects on the VPS."""
    
    def __init__(self, projects_dir: str = Config.PROJECTS_DIR):
        self.projects_dir = projects_dir
        os.makedirs(self.projects_dir, exist_ok=True)
        
        # Configure git author details matching Flora's identity
        self.author = Actor("Flora (CTO Agent)", "flora-cto@example.com")

    def _get_repo_path(self, repo_name: str) -> str:
        """Helper to get local absolute path of a project repository."""
        return os.path.join(self.projects_dir, repo_name)

    def _get_authenticated_url(self, repo_url: str) -> str:
        """Inject GitHub Personal Access Token into https repo URL for headless authentication."""
        if not Config.GITHUB_TOKEN:
            return repo_url
            
        if repo_url.startswith("https://"):
            # Format: https://<token>@github.com/username/repo.git
            clean_url = repo_url.replace("https://", "")
            return f"https://{Config.GITHUB_TOKEN}@{clean_url}"
        elif repo_url.startswith("git@github.com:"):
            # If the user provides SSH, but we have an HTTPS token, convert it to secure HTTPS
            clean_url = repo_url.replace("git@github.com:", "").replace(".git", "")
            return f"https://{Config.GITHUB_TOKEN}@github.com/{clean_url}.git"
            
        return repo_url

    def clone_repository(self, repo_url: str, repo_name: str) -> dict:
        """Clone a remote GitHub repository to the server."""
        repo_path = self._get_repo_path(repo_name)
        
        # Clean up existing folder if any to avoid conflicts
        if os.path.exists(repo_path):
            logger.warning(f"Directory {repo_path} already exists. Deleting it first.")
            shutil.rmtree(repo_path)
            
        auth_url = self._get_authenticated_url(repo_url)
        logger.info(f"Cloning {repo_url} into {repo_path}...")
        
        try:
            repo = Repo.clone_from(auth_url, repo_path)
            # Ensure branch is main or master
            active_branch = repo.active_branch.name
            logger.info(f"Successfully cloned repository. Active branch: {active_branch}")
            return {
                "success": True,
                "path": repo_path,
                "branch": active_branch,
                "message": f"Репозиторий успешно склонирован в папку `{repo_name}`!"
            }
        except Exception as e:
            logger.error(f"Failed to clone repository: {e}")
            return {"success": False, "error": str(e)}

    def pull_changes(self, repo_name: str) -> dict:
        """Pull latest changes from remote repository."""
        repo_path = self._get_repo_path(repo_name)
        try:
            repo = Repo(repo_path)
            origin = repo.remotes.origin
            
            # Setup authentication for remote fetch
            origin.set_url(self._get_authenticated_url(origin.url))
            
            info = origin.pull()
            logger.info(f"Pulled changes for {repo_name}")
            return {
                "success": True,
                "message": f"Изменения успешно стянуты с GitHub! Актуальный коммит: {repo.head.commit.hexsha[:7]}"
            }
        except Exception as e:
            logger.error(f"Failed to pull changes: {e}")
            return {"success": False, "error": str(e)}

    def create_and_checkout_branch(self, repo_name: str, branch_name: str) -> dict:
        """Create a new git branch and switch to it."""
        repo_path = self._get_repo_path(repo_name)
        try:
            repo = Repo(repo_path)
            # Create branch if it doesn't exist, then checkout
            if branch_name in repo.branches:
                new_branch = repo.branches[branch_name]
            else:
                new_branch = repo.create_head(branch_name)
                
            new_branch.checkout()
            logger.info(f"Switched to branch {branch_name} in {repo_name}")
            return {
                "success": True,
                "message": f"Создала и переключилась на новую ветку `{branch_name}`."
            }
        except Exception as e:
            logger.error(f"Failed to create/checkout branch: {e}")
            return {"success": False, "error": str(e)}

    def commit_and_push(self, repo_name: str, commit_message: str, branch_name: str = None) -> dict:
        """Stage all changes, commit them, and push to GitHub."""
        repo_path = self._get_repo_path(repo_name)
        try:
            repo = Repo(repo_path)
            
            # If target branch specified, ensure we are on it
            if branch_name:
                self.create_and_checkout_branch(repo_name, branch_name)
                
            # Stage all changes (git add .)
            repo.git.add(A=True)
            
            # Check if there are actually any changes to commit
            if not repo.is_dirty(untracked_files=True):
                return {
                    "success": True,
                    "message": "Изменений для коммита не обнаружено. Всё уже синхронизировано!"
                }
                
            # Commit changes as Flora (CTO Agent)
            commit = repo.index.commit(commit_message, author=self.author, committer=self.author)
            logger.info(f"Committed changes: {commit.hexsha[:7]}")
            
            # Push changes to origin
            origin = repo.remotes.origin
            origin.set_url(self._get_authenticated_url(origin.url))
            
            current_branch = repo.active_branch.name
            origin.push(refspec=f"{current_branch}:{current_branch}")
            logger.info(f"Pushed branch {current_branch} to origin")
            
            return {
                "success": True,
                "commit_hash": commit.hexsha[:7],
                "message": f"Изменения успешно закоммичены с описанием: '{commit_message}' и запушены в ветку `{current_branch}` на GitHub! 🚀"
            }
        except Exception as e:
            logger.error(f"Failed to commit and push: {e}")
            return {"success": False, "error": str(e)}

    def get_status(self, repo_name: str) -> dict:
        """Get git status of the local repository."""
        repo_path = self._get_repo_path(repo_name)
        try:
            repo = Repo(repo_path)
            is_dirty = repo.is_dirty(untracked_files=True)
            untracked = repo.untracked_files
            changed_files = [item.a_path for item in repo.index.diff(None)]
            staged_files = [item.a_path for item in repo.index.diff("HEAD")]
            
            return {
                "success": True,
                "active_branch": repo.active_branch.name,
                "is_dirty": is_dirty,
                "untracked_files": untracked,
                "changed_files": changed_files,
                "staged_files": staged_files
            }
        except Exception as e:
            logger.error(f"Failed to get git status: {e}")
            return {"success": False, "error": str(e)}
            
    def get_file_content(self, repo_name: str, file_path: str) -> dict:
        """Read content of a specific file in the project."""
        repo_path = self._get_repo_path(repo_name)
        full_path = os.path.join(repo_path, file_path)
        
        # Security check to prevent path traversal outside projects
        if not os.path.abspath(full_path).startswith(os.path.abspath(repo_path)):
            return {"success": False, "error": "Access denied: Path traversal detected."}
            
        try:
            if not os.path.exists(full_path):
                return {"success": False, "error": f"Файл `{file_path}` не найден."}
                
            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()
                
            return {"success": True, "content": content}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def write_file_content(self, repo_name: str, file_path: str, content: str) -> dict:
        """Write/Edit content of a specific file in the project."""
        repo_path = self._get_repo_path(repo_name)
        full_path = os.path.join(repo_path, file_path)
        
        if not os.path.abspath(full_path).startswith(os.path.abspath(repo_path)):
            return {"success": False, "error": "Access denied: Path traversal detected."}
            
        try:
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
                
            return {"success": True, "message": f"Файл `{file_path}` успешно изменен/создан!"}
        except Exception as e:
            return {"success": False, "error": str(e)}
