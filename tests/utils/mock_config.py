from openjiuwen_deepsearch.config.config import AgentConfig


def get_default_agent_config():
    agent_config=AgentConfig().model_dump()
    agent_config["llm_config"]["model_name"] = "mock_model"
    agent_config["llm_config"]["api_key"] = bytearray("xxx", encoding="utf-8")
    agent_config["llm_config"]["base_url"] = "https://example.com"
    return agent_config
