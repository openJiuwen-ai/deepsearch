from openjiuwen_deepsearch.config.config import AgentConfig, LLMConfig


def get_default_agent_config():
    llm_config = {
        "general": LLMConfig(
            model_name="mock_model",
            api_key=bytearray("xxx", encoding="utf-8"),
            base_url="https://example.com"
        ).model_dump()
    }
    agent_config=AgentConfig().model_dump()
    agent_config["llm_config"] = llm_config
    return agent_config
