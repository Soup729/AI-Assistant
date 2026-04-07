import asyncio
import json
import queue
import threading
from typing import AsyncGenerator, Dict, Generator, List, Optional

import httpx

from app.storage.config import ModelProfile, config_manager
from app.utils.logger import logger


class LLMClient:
    def _build_chat_url(self, base_url: str) -> str:
        base = (base_url or "").strip().rstrip("/")
        if not base:
            return "https://api.openai.com/v1/chat/completions"
        if base.endswith("/chat/completions"):
            return base
        if "/v1" not in base and "openai" in base.lower():
            return f"{base}/v1/chat/completions"
        return f"{base}/chat/completions"

    def _profile(self, model_profile: Optional[ModelProfile]) -> ModelProfile:
        return model_profile or config_manager.get_active_model_profile()

    def _build_payload(self, messages: List[Dict[str, str]], profile: ModelProfile) -> Dict[str, object]:
        return {
            "model": profile.model_name,
            "messages": messages,
            "temperature": profile.temperature,
            "max_tokens": profile.max_tokens,
            "stream": True,
        }

    def _error_message_from_response(self, response_text: str, status_code: int) -> str:
        error_msg = f"HTTP {status_code}"
        try:
            payload = json.loads(response_text)
            error_msg = payload.get("error", {}).get("message", response_text) or error_msg
        except Exception:
            if response_text.strip():
                error_msg = response_text.strip()
        return error_msg

    async def achat_stream(
        self,
        messages: List[Dict[str, str]],
        model_profile: Optional[ModelProfile] = None,
    ) -> AsyncGenerator[str, None]:
        """
        使用同步 httpx + run_in_executor 实现流式请求。

        原因：httpx AsyncClient 在任务取消时，其内部的 aclose() coroutine 对象
        被 Python GC 回收，Python runtime 的 coroutine.__del__ 在无 event loop
        的上下文中执行 httpx 清理代码，导致：
        - "async generator ignored GeneratorExit"
        - "no running event loop"

        改用同步 httpx：所有网络操作在独立线程中，close() 是普通方法调用，
        无 coroutine 对象，无任何 async 清理问题。
        """
        profile = self._profile(model_profile)
        api_url = self._build_chat_url(profile.api_base_url)
        headers = {
            "Authorization": f"Bearer {profile.api_key}",
            "Content-Type": "application/json",
        }
        payload = self._build_payload(messages, profile)

        logger.info(f"请求模型接口: {api_url}, 模型: {profile.display_name} / {profile.model_name}")

        chunk_queue: queue.Queue = queue.Queue()
        thread_done = threading.Event()
        error_info = {"error": None}
        should_stop = False

        def _sync_stream():
            """在独立线程中执行同步 httpx 流式请求"""
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
                        raw = response.read().decode(errors="ignore")
                        error_msg = self._error_message_from_response(raw, response.status_code)
                        logger.error(f"API 请求失败: {error_msg}")
                        error_info["error"] = f"Error: API 请求失败 ({error_msg})"
                        return

                    for line in response.iter_lines():
                        if should_stop:
                            break
                        if not line or not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break

                        try:
                            data = json.loads(data_str)
                        except Exception:
                            continue

                        choices = data.get("choices", [])
                        if not choices:
                            continue
                        delta = choices[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            try:
                                chunk_queue.put(content, timeout=0.5)
                            except queue.Full:
                                # 主线程消费太慢，停止发送
                                break
            except Exception as e:
                logger.error(f"同步流线程异常: {e}")
                error_info["error"] = f"Error: 发生意外错误 ({str(e)})"
            finally:
                thread_done.set()

        try:
            thread = threading.Thread(target=_sync_stream, daemon=True)
            thread.start()

            while True:
                try:
                    chunk = await asyncio.wait_for(
                        asyncio.get_running_loop().run_in_executor(
                            None, chunk_queue.get, True, 0.05
                        ),
                        timeout=1.0,
                    )
                    yield chunk
                except asyncio.TimeoutError:
                    # 定期让出控制权，检查取消信号
                    await asyncio.sleep(0)
                    if thread_done.is_set() and chunk_queue.empty():
                        break
                except queue.Empty:
                    if thread_done.is_set():
                        break
        except asyncio.CancelledError:
            should_stop = True
            logger.debug("LLM 流式请求被取消")
            if thread.is_alive():
                thread.join(timeout=1.0)
            raise
        finally:
            should_stop = True
            if thread.is_alive():
                thread.join(timeout=1.0)

        if error_info["error"]:
            yield error_info["error"]

    def chat_stream(
        self,
        messages: List[Dict[str, str]],
        model_profile: Optional[ModelProfile] = None,
    ) -> Generator[str, None, None]:
        profile = self._profile(model_profile)
        api_url = self._build_chat_url(profile.api_base_url)
        headers = {
            "Authorization": f"Bearer {profile.api_key}",
            "Content-Type": "application/json",
        }
        payload = self._build_payload(messages, profile)

        logger.info(f"请求模型接口: {api_url}, 模型: {profile.display_name} / {profile.model_name}")

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
                    raw = response.read().decode(errors="ignore")
                    error_msg = self._error_message_from_response(raw, response.status_code)
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
                    except Exception:
                        continue

                    choices = data.get("choices", [])
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        yield content

        except httpx.ConnectError:
            logger.error("无法连接到模型服务")
            yield "Error: 无法连接到模型服务，请检查 API 地址或网络代理。"
        except httpx.TimeoutException:
            logger.error("模型请求超时")
            yield "Error: 模型请求超时，请稍后重试。"
        except Exception as e:
            logger.error(f"模型请求异常: {e}")
            yield f"Error: 发生意外错误 ({str(e)})"


llm_client = LLMClient()
