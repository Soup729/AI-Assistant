import httpx
import json
from typing import Generator, List, Dict
from app.storage.config import config
from app.utils.logger import logger

class LLMClient:
    def __init__(self):
        pass

    def _get_api_url(self) -> str:
        """
        根据配置动态生成完整的 API URL
        """
        base_url = config.api_base_url.rstrip('/')
        
        # 如果用户填写的已经是完整路径，则直接返回
        if base_url.endswith('/chat/completions'):
            return base_url
            
        # 否则尝试拼接标准路径
        # 兼容性处理：有些平台需要 /v1，有些不需要
        if '/v1' not in base_url and 'openai' in base_url.lower():
            return f"{base_url}/v1/chat/completions"
        
        return f"{base_url}/chat/completions"

    def chat_stream(self, messages: List[Dict[str, str]]) -> Generator[str, None, None]:
        """
        发起流式对话请求，支持所有 OpenAI 兼容接口
        """
        api_url = self._get_api_url()
        headers = {
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json"
        }
        
        # 构造请求体
        payload = {
            "model": config.model_name,
            "messages": messages,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "stream": True
        }
        
        logger.info(f"请求 API: {api_url}, 模型: {config.model_name}")
        
        try:
            # 使用 httpx 发起流式请求
            with httpx.stream(
                "POST", 
                api_url, 
                headers=headers, 
                json=payload, 
                timeout=60.0, # 增加超时时间以支持更慢的模型
                follow_redirects=True
            ) as response:
                if response.status_code != 200:
                    # 尝试读取错误信息
                    try:
                        error_data = response.read().decode()
                        error_json = json.loads(error_data)
                        error_msg = error_json.get("error", {}).get("message", error_data)
                    except:
                        error_msg = f"HTTP {response.status_code}"
                        
                    logger.error(f"API 请求失败: {error_msg}")
                    yield f"Error: API 请求失败 ({error_msg})"
                    return

                for line in response.iter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    
                    data_str = line[6:] # 去掉 "data: "
                    if data_str.strip() == "[DONE]":
                        break
                        
                    try:
                        data = json.loads(data_str)
                        # 兼容不同厂商的响应格式
                        choices = data.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield content
                    except Exception as e:
                        # 忽略解析错误的行（有些厂商可能会发非 JSON 数据）
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
