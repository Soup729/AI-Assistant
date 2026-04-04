import json
from typing import Dict, Generator, List, Optional

import httpx

from app.storage.config import ModelProfile, config_manager
from app.utils.logger import logger


class LLMClient:
    def __init__(self):
        pass

    def _get_api_url(self, base_url: str) -> str:
        """
        根据配置动态生成完整的 API URL。
        """
        base_url = base_url.rstrip("/")

        if base_url.endswith("/chat/completions"):
            return base_url

        if "/v1" not in base_url and "openai" in base_url.lower():
            return f"{base_url}/v1/chat/completions"

        return f"{base_url}/chat/completions"

    def chat_stream(
        self,
        messages: List[Dict[str, str]],
        model_profile: Optional[ModelProfile] = None,
    ) -> Generator[str, None, None]:
        """
        发起流式对话请求，支持 OpenAI 兼容接口。
        """
        profile = model_profile or config_manager.get_active_model_profile()
        api_url = self._get_api_url(profile.api_base_url)
        headers = {
            "Authorization": f"Bearer {profile.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": profile.model_name,
            "messages": messages,
            "temperature": profile.temperature,
            "max_tokens": profile.max_tokens,
            "stream": True,
        }

        logger.info(f"请求 API: {api_url}, 模型档案: {profile.display_name} / {profile.model_name}")

        try:
            with httpx.stream(
                "POST",
                api_url,
                headers=headers,
                json=payload,
                timeout=60.0,
                follow_redirects=True,
            ) as response:
                if response.status_code != 200:
                    try:
                        error_data = response.read().decode()
                        error_json = json.loads(error_data)
                        error_msg = error_json.get("error", {}).get("message", error_data)
                    except Exception:
                        error_msg = f"HTTP {response.status_code}"

                    logger.error(f"API 请求失败: {error_msg}")
                    yield f"Error: API 请求失败 ({error_msg})"
                    return

                for line in response.iter_lines():
                    if not line or not line.startswith("data: "):
                        continue

                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break

                    try:
                        data = json.loads(data_str)
                        choices = data.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield content
                    except Exception:
                        continue

        except httpx.ConnectError:
            logger.error("无法连接到服务器")
            yield "Error: 无法连接到服务器，请检查 API 地址或网络代理。"
        except httpx.TimeoutException:
            logger.error("请求超时")
            yield "Error: 请求超时，请稍后重试。"
        except Exception as e:
            logger.error(f"请求发生异常: {e}")
            yield f"Error: 发生意外错误 ({str(e)})"


llm_client = LLMClient()
