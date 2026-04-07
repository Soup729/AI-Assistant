import asyncio
import gc
import json
import sys
import traceback
from typing import AsyncGenerator, Dict, Generator, List, Optional

import httpx

from app.storage.config import ModelProfile, config_manager
from app.utils.logger import logger


def _check_event_loop() -> bool:
    """检查是否有可用的事件循环"""
    try:
        if sys.version_info >= (3, 10):
            import asyncio
            asyncio.get_running_loop()
        else:
            asyncio.get_event_loop()
        return True
    except RuntimeError:
        return False


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
        profile = self._profile(model_profile)
        api_url = self._build_chat_url(profile.api_base_url)
        headers = {
            "Authorization": f"Bearer {profile.api_key}",
            "Content-Type": "application/json",
        }
        payload = self._build_payload(messages, profile)

        logger.info(f"请求模型接口: {api_url}, 模型: {profile.display_name} / {profile.model_name}")

        # 手动管理资源，不使用 stream() 上下文管理器
        # 这样可以更精确地控制清理时机
        client: httpx.AsyncClient | None = None
        stream_ctx = None
        response: httpx.Response | None = None
        line_iter: AsyncGenerator[str, None] | None = None

        try:
            client = httpx.AsyncClient(timeout=60.0, follow_redirects=True)
            stream_ctx = client.stream("POST", api_url, headers=headers, json=payload)
            response = await stream_ctx.__aenter__()

            if response.status_code != 200:
                raw = await response.aread()
                error_msg = self._error_message_from_response(raw.decode(errors="ignore"), response.status_code)
                logger.error(f"API 请求失败: {error_msg}")
                yield f"Error: API 请求失败 ({error_msg})"
                return

            # 获取行迭代器
            line_iter = response.aiter_lines()
            async for line in line_iter:
                # 让出控制权，确保取消信号能及时传播
                await asyncio.sleep(0)

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

        except asyncio.CancelledError:
            logger.debug("LLM 流式请求被取消")
            raise
        except httpx.ConnectError:
            logger.error("无法连接到模型服务")
            yield "Error: 无法连接到模型服务，请检查 API 地址或网络代理。"
        except httpx.TimeoutException:
            logger.error("模型请求超时")
            yield "Error: 模型请求超时，请稍后重试。"
        except Exception as e:
            logger.error(f"模型请求异常: {e}")
            yield f"Error: 发生意外错误 ({str(e)})"
        finally:
            # 清理资源：按照创建的反序释放
            # 注意：这里的清理可能在没有事件循环的上下文中被调用
            # 因此使用 run_coroutine_threadsafe 或检查事件循环
            if line_iter is not None:
                try:
                    # 尝试关闭行迭代器
                    if hasattr(line_iter, 'aclose'):
                        aclose = line_iter.aclose()
                        if asyncio.iscoroutine(aclose):
                            # 如果事件循环可用，await 协程
                            if _check_event_loop():
                                try:
                                    await aclose
                                except asyncio.CancelledError:
                                    pass
                                except RuntimeError as e:
                                    if "no running event loop" not in str(e):
                                        raise
                                except Exception:
                                    pass
                except RuntimeError as e:
                    if "no running event loop" not in str(e):
                        logger.warning(f"关闭行迭代器时异常: {e}")
                except Exception as e:
                    logger.warning(f"关闭行迭代器时异常: {e}")

            if stream_ctx is not None:
                try:
                    await stream_ctx.__aexit__(None, None, None)
                except RuntimeError as e:
                    logger.warning(f"关闭流式上下文时异常: {e}")
                except Exception as e:
                    logger.warning(f"关闭流式上下文时异常: {e}")
            elif response is not None:
                try:
                    response.close()
                except Exception:
                    pass

            if client is not None:
                try:
                    await client.aclose()
                except RuntimeError as e:
                    if "no running event loop" not in str(e):
                        logger.warning(f"关闭客户端时异常: {e}")
                except Exception:
                    pass

            # 强制垃圾回收以确保生成器被正确清理
            gc.collect()

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
