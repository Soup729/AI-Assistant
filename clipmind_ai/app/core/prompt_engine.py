from typing import List, Dict, Any, Optional
from app.storage.db import db_manager
from app.utils.logger import logger

class PromptEngine:
    def __init__(self):
        self.templates = self._load_templates()

    def _load_templates(self) -> List[Dict[str, Any]]:
        try:
            return db_manager.get_templates()
        except Exception as e:
            logger.error(f"加载 Prompt 模板失败: {e}")
            return []

    def refresh_templates(self):
        self.templates = self._load_templates()

    def get_template_names(self) -> List[str]:
        return [t["name"] for t in self.templates]

    def format_prompt(self, template_name: str, user_input: str, context: Optional[str] = None) -> List[Dict[str, str]]:
        """
        根据模板名称和用户输入，生成用于对话的 messages 列表。
        支持在 user_prompt_template 中使用 {text} 占位符。
        如果提供了 context（如搜索结果），可以拼接到 input 中。
        """
        template = next((t for t in self.templates if t["name"] == template_name), None)
        if not template:
            # 降级：默认无模板直接对话
            return [
                {"role": "system", "content": "你是一个助手。"},
                {"role": "user", "content": user_input}
            ]

        system_content = template["system_prompt"]
        user_template = template["user_prompt_template"]
        
        # 处理搜索上下文
        if context:
            user_input = f"参考上下文信息：\n{context}\n\n问题：{user_input}"
            
        # 填充模板
        try:
            user_content = user_template.replace("{text}", user_input)
        except Exception:
            user_content = user_input

        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content}
        ]

    def is_search_enabled(self, template_name: str) -> bool:
        template = next((t for t in self.templates if t["name"] == template_name), None)
        return bool(template.get("enable_search", False)) if template else False

prompt_engine = PromptEngine()
