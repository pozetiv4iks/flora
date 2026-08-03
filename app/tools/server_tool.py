import os
import subprocess
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

class ServerTool:
    """Provides tools for direct system interaction, shell execution, and SSH key management."""
    
    def __init__(self):
        self.ssh_dir = os.path.expanduser("~/.ssh")
        os.makedirs(self.ssh_dir, mode=0o700, exist_ok=True)
        
        # Defensive: Automatically configure SSH to ignore unknown host prompt (for non-interactive headless git pull/push)
        config_path = os.path.join(self.ssh_dir, "config")
        try:
            # We write StrictHostKeyChecking no to ssh config so git doesn't hang on prompts
            with open(config_path, "w", encoding="utf-8") as f:
                f.write("Host github.com\n    StrictHostKeyChecking no\n    UserKnownHostsFile /dev/null\n")
            os.chmod(config_path, 0o600)
            logger.info("Successfully configured SSH StrictHostKeyChecking no in ~/.ssh/config")
        except Exception as e:
            logger.error(f"Failed to write SSH config: {e}")

    def run_command(self, command: str, timeout: int = 60) -> Dict[str, Any]:
        """Run a shell command inside the container (with Docker socket access for host control)."""
        logger.info(f"Executing system command: {command}")
        try:
            # We run the command using /bin/bash (or /bin/sh if bash is missing)
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                executable="/bin/bash" if os.path.exists("/bin/bash") else None
            )
            
            return {
                "success": result.returncode == 0,
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr
            }
        except subprocess.TimeoutExpired as te:
            logger.error(f"Command timeout: {command}")
            return {
                "success": False,
                "error": f"Command timed out after {timeout} seconds.",
                "stdout": te.stdout or "",
                "stderr": te.stderr or ""
            }
        except Exception as e:
            logger.error(f"Error running command: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    def generate_ssh_key(self, key_name: str = "id_ed25519") -> Dict[str, Any]:
        """Generate a secure SSH key pair in ~/.ssh/ if it doesn't already exist."""
        key_path = os.path.join(self.ssh_dir, key_name)
        pub_path = f"{key_path}.pub"
        
        if os.path.exists(key_path):
            try:
                with open(pub_path, "r", encoding="utf-8") as f:
                    pub_key = f.read().strip()
                return {
                    "success": True,
                    "created": False,
                    "public_key": pub_key,
                    "message": f"SSH-ключ уже существует по пути: `{key_path}`"
                }
            except Exception as e:
                return {"success": False, "error": f"Ключ существует, но не удалось прочитать публичную часть: {e}"}
        
        # Command to generate non-interactive ED25519 key without passphrase
        cmd = f'ssh-keygen -t ed25519 -N "" -f {key_path}'
        res = self.run_command(cmd)
        
        if res["success"]:
            try:
                # Set permissions
                os.chmod(key_path, 0o600)
                if os.path.exists(pub_path):
                    os.chmod(pub_path, 0o644)
                
                with open(pub_path, "r", encoding="utf-8") as f:
                    pub_key = f.read().strip()
                
                return {
                    "success": True,
                    "created": True,
                    "public_key": pub_key,
                    "message": f"Успешно создала новую пару SSH-ключей `{key_name}`!"
                }
            except Exception as e:
                return {"success": False, "error": f"Ключ сгенерирован, но не удалось его прочесть: {e}"}
        else:
            return {
                "success": False,
                "error": f"Ошибка генерации SSH-ключа: {res.get('stderr') or res.get('error')}"
            }
