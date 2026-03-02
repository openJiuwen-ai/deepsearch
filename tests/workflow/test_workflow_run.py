import logging
from copy import deepcopy
from typing import Any

import pytest

from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.config.config import Config
from openjiuwen_deepsearch.framework.openjiuwen.agent.agent_factory import AgentFactory
from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import DeepresearchAgent

logger = logging.getLogger(__name__)


async def validate_run_input_parameter(
        param_name: str,
        invalid_value: Any,
        error_code: int,
        error_msg_fragment: str,
        base_config: dict
) -> None:
    """验证 run 方法输入参数的公共逻辑"""
    current_config = deepcopy(base_config)
    agent_factory = AgentFactory()
    agent = agent_factory.create_agent(current_config)

    explicit_params = {
        "message": "hello",  # 默认值
        "conversation_id": "default_session_id",
        "report_template": "",
        "interrupt_feedback": "",
        "agent_config": current_config,
    }
    explicit_params[param_name] = invalid_value

    with pytest.raises(CustomValueException) as exc_info:
        async for _ in agent.run(**explicit_params):
            pass  # 消费异步生成器

    err_msg = str(exc_info.value)
    logger.info(f"error_info: {err_msg}")
    assert exc_info.value.error_code == error_code
    assert error_msg_fragment in err_msg


# 测试用例1: message 参数验证
@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_value, error_code, error_msg_fragment", [
    (None, 200011, "Parameter validation failed, type of feild 'message' must not be empty"),
    ("", 200011, "Parameter validation failed, type of feild 'message' must not be empty"),
    (1, 200010, "Parameter validation failed, type of feild 'message' must be str")
])
async def test_run_validate_message(invalid_value, error_code, error_msg_fragment):
    await validate_run_input_parameter(
        "message",
        invalid_value,
        error_code,
        error_msg_fragment,
        Config().agent_config.model_dump()
    )


# 测试用例2: conversation_id 参数验证
@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_value, error_code, error_msg_fragment", [
    (None, 200011, "Parameter validation failed, type of feild 'conversation_id' must not be empty"),
    ("", 200011, "Parameter validation failed, type of feild 'conversation_id' must not be empty"),
    (1, 200010, "Parameter validation failed, type of feild 'conversation_id' must be str")
])
async def test_run_validate_conversation_id(invalid_value, error_code, error_msg_fragment):
    await validate_run_input_parameter(
        "conversation_id",
        invalid_value,
        error_code,
        error_msg_fragment,
        Config().agent_config.model_dump()
    )


# 测试用例3: report_template 参数验证
@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_value, error_code, error_msg_fragment", [
    (123, 200010, "Parameter validation failed, type of feild 'report_template' must be str"),
])
async def test_run_validate_report_template(invalid_value, error_code, error_msg_fragment):
    await validate_run_input_parameter(
        "report_template",
        invalid_value,
        error_code,
        error_msg_fragment,
        Config().agent_config.model_dump()
    )


# 测试用例4: interrupt_feedback 参数验证
@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_value, error_code, error_msg_fragment", [
    (123, 200010, "Parameter validation failed, type of feild 'interrupt_feedback' must be str"),
    ("xxx", 200012, "Parameter 'interrupt_feedback' must be either an empty string or 'accepted' or 'cancel'"),
])
async def test_run_validate_interrupt_feedback(invalid_value, error_code, error_msg_fragment):
    await validate_run_input_parameter(
        "interrupt_feedback",
        invalid_value,
        error_code,
        error_msg_fragment,
        Config().agent_config.model_dump()
    )


# 测试用例5: agent_config 参数验证
wrong_agent_config = Config().agent_config.model_dump()
wrong_agent_config["outliner_max_section_num"] = -1


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_value, error_code, error_msg_fragment", [
    (wrong_agent_config, 200009, "Parameter validation failed"),
    (None, 200011, "type of feild 'agent_config' must not be empty"),
])
async def test_run_validate_agent_config(invalid_value, error_code, error_msg_fragment):
    await validate_run_input_parameter(
        "agent_config",
        invalid_value,
        error_code,
        error_msg_fragment,
        Config().agent_config.model_dump()
    )


# 测试用例6: 处理报告模板
def test_handle_report_template():
    agent = DeepresearchAgent()
    report_template = """IyDkuIDjgIEg566X5Yqb57uP5rWO55CG6K665Z+656GACj4gaXNfY29yZV9zZWN0aW9uOiBmYWxzZQo+IOWKn+iDveamgui/
    sDog57O757uf6ZiQ6L+w566X5Yqb57uP5rWO55qE5Z+65pys5qaC5b+144CB5pS/562W6IOM5pmv44CB5a6a5LmJ5ZKM5YaF5ra177yM5Li65ZCO57ut5
    6ug6IqC5o+Q5L6b55CG6K665pSv5pKRCgojIyDvvIjkuIDvvInnrpflipvnu4/mtY7mpoLlv7Xog4zmma8KPiDlip/og73mpoLov7A6IOmYkOi/sOeul+
    WKm+S7juaKgOacr+aMh+agh+WIsOe7j+a1jumpseWKqOWKm+eahOi9rOWPmOi/h+eoi++8jOWIhuaekOeul+WKm+e7j+a1juWFtOi1t+eahOiDjOaZr+W
    SjOWtpuacr+eglOeptueOsOeKtgoKIyMg77yI5LqM77yJ566X5Yqb57uP5rWO5pS/562W6IOM5pmvCj4g5Yqf6IO95qaC6L+wOiDliIbmnpDlm73pmYXku
    LvopoHlm73lrrblkozkuK3lm73lnKjnrpflipvnu4/mtY7poobln5/nmoTmlL/nrZbluIPlsYDlkozlrp7ot7XkuL7mjqoKCiMjIO+8iOS4ie+8ieeul+W
    Km+e7j+a1jueahOWumuS5iQo+IOWKn+iDveamgui/sDog5piO56Gu5a6a5LmJ566X5Yqb57uP5rWO5qaC5b+177yM5Yy65YiG5b6u6KeC5ZKM5a6P6KeC5
    Lik5Liq57u05bqm55qE56CU56m25a+56LGh5ZKM55uu5qCHCgojIyDvvIjlm5vvvInnrpflipvnu4/mtY7nmoTlhoXmtrUKPiDlip/og73mpoLov7A6IOS
    7juS6lOS4quaWuemdoua3seWFpeino+aekOeul+WKm+e7j+a1jueahOWGhea2teeJueW+ge+8jOmYkOi/sOWFtuS4juaVsOWtl+e7j+a1jueahOWFs+ezu
    woKIyDkuozjgIEg566X5Yqb57uP5rWO6L+Q6KGM5py655CGCj4gaXNfY29yZV9zZWN0aW9uOiB0cnVlCj4g5Yqf6IO95qaC6L+wOiDor6bnu4bliIbmnpD
    nrpflipvnu4/mtY7lnKjlvq7op4LkvIHkuJrlkozlro/op4LnpL7kvJrkuKTkuKrlsYLpnaLnmoTov5DooYzmnLrliLblkozkvZznlKjljp/nkIYKCiMjI
    O+8iOS4gO+8ieW+ruingueul+WKm+e7j+a1jui/kOihjOacuueQhgo+IOWKn+iDveamgui/sDog5YiG5p6Q566X5Yqb57uP5rWO5aaC5L2V5o+Q5Y2H5LyB
    5Lia5oqA5pyv6IO95Yqb44CB5LyY5YyW5Y+R5bGV5qih5byP44CB6ZmN5L2O5oiQ5pys5ZKM5pW05ZCI6LWE5rqQCgojIyDvvIjkuozvvInlro/op4Lnrpf
    lipvnu4/mtY7ov5DooYzmnLrnkIYKPiDlip/og73mpoLov7A6IOmYkOi/sOeul+WKm+e7j+a1juWmguS9leaPkOWNh+WFqOimgee0oOeUn+S6p+eOh+OAge
    S/g+i/m+S6p+S4mue7k+aehOS8mOWMluOAgeiKgue6pueUn+S6p+imgee0oOWSjOaOqOWKqOe7v+iJsuWPkeWxlQoKIyDkuInjgIEg5oiR5Zu9566X5Yqb5
    7uP5rWO5Y+R5bGV546w54q2Cj4gaXNfY29yZV9zZWN0aW9uOiB0cnVlCj4g5Yqf6IO95qaC6L+wOiDlhajpnaLliIbmnpDkuK3lm73nrpflipvnu4/mtY7l
    nKjotYTmnKzjgIHkurrmiY3jgIHog73mupDopoHntKDpqbHliqjkuIvnmoTlj5HlsZXnjrDnirblkozkvpvpnIDmg4XlhrUKCiMjIO+8iOS4gO+8ieeul+W
    Km+e7j+a1juWPl+imgee0oOmpseWKqOW9seWTjQo+IOWKn+iDveamgui/sDog5YiG5p6Q6LWE5pys44CB5Lq65omN44CB6IO95rqQ5LiJ5aSn6KaB57Sg5a
    +5566X5Yqb57uP5rWO5Y+R5bGV55qE6amx5Yqo5L2c55So5ZKM5YW35L2T6KGo546wCgojIyDvvIjkuozvvInnrpflipvnu4/mtY7kvpvpnIDkuKTnq6/lk
    Izlj5HlipsKPiDlip/og73mpoLov7A6IOWIhuaekOeul+WKm+mcgOaxguerr+eahOaKgOacr+mpseWKqOWSjOS+m+e7meerr+eahOiDveWKm+aPkOWNh+OA
    gee7k+aehOS8mOWMluWSjOaooeW8j+WIm+aWsAoKIyMg77yI5LiJ77yJ566X5Yqb57uP5rWO55qE5rqi5Ye65pWI5bqU5pi+6JGXCj4g5Yqf6IO95qaC6L+
    wOiDpmJDov7Dnrpflipvnu4/mtY7lr7lHRFDlop7plb/nmoTkv4Pov5vkvZznlKjlj4rlhbblnKjkuI3lkIzlnLDljLrlkozmlL/nrZbnjq/looPkuIvnmo
    Tlt67lvILmgKflvbHlk40KCiMg5Zub44CBIOeul+WKm+e7j+a1juWPkeWxleS8mOengOahiOS+iwo+IGlzX2NvcmVfc2VjdGlvbjogdHJ1ZQo+IOWKn+iDv
    eamgui/sDog6YCa6L+H5YW35L2T5qGI5L6L5bGV56S65Zyw5pa55pS/5bqc5ZKM5LyB5Lia5o6i57Si566X5Yqb57uP5rWO5Y+R5bGV6Lev5b6E55qE5Yib
    5paw5a6e6Le15ZKM5oiQ5Yqf57uP6aqMCgojIyDvvIjkuIDvvInlnLDmlrnnibnoibLmqKHlvI/mjqLntKLnrpflipvnu4/mtY7lj5HlsZXot6/lvoQKPiD
    lip/og73mpoLov7A6IOS7i+e7jeW5s+a5luW4guOAgeahkOS5oeW4guOAgem+mea4uOWOv+etieWcsOaWueaOoue0oueul+WKm+e7j+a1juWPkeWxleeahO
    eJueiJsuaooeW8j+WSjOWunui3tee7j+mqjAoKIyMg77yI5LqM77yJ5LyB5Lia5Yib5paw5a6e6Le15Yqp5Yqb566X5Yqb5Lqn5Lia5LyY5YyW5Y2H57qnC
    j4g5Yqf6IO95qaC6L+wOiDliIbmnpDmt7HlnLPmmbrln47nv7zkupHjgIHovrDoh7Tpm4blm6LjgIHmtarmva7kv6Hmga/nrYnkvIHkuJrlnKjnrpflipvk
    uqfkuJrliJvmlrDmlrnpnaLnmoTlrp7ot7XmoYjkvosKCiMg5LqU44CBIOWItue6puaIkeWbveeul+WKm+e7j+a1juWPkeWxleeahOS4u+imgemXrumimAo
    +IGlzX2NvcmVfc2VjdGlvbjogZmFsc2UKPiDlip/og73mpoLov7A6IOezu+e7n+WIhuaekOW9k+WJjeWItue6puS4reWbveeul+WKm+e7j+a1juWPkeWxle
    eahOWbm+S4quS4u+imgemXrumimOWSjOaMkeaImAoKIyMg77yI5LiA77yJIOaImOeVpeinhOWIkue7n+etueS4jei2s++8jOWItuW6puaUr+aSkeS9k+ezu
    +acieW+heWujOWWhAo+IOWKn+iDveamgui/sDog5YiG5p6Q5oiY55Wl6KeE5YiS57y65aSx5ZKM5Yi25bqm5pSv5pKR5LiN6Laz5a+5566X5Yqb57uP5rWO
    5Y+R5bGV55qE5b2x5ZONCgojIyDvvIjkuozvvIkg5Yy65Z+f5Y+R5bGV6Lev5b6E5qih57OK77yM5beu5byC5YyW5Y+R5bGV5qih5byP5b6F5o6i57SiCj4
    g5Yqf6IO95qaC6L+wOiDmjqLorqjljLrln5/lj5HlsZXot6/lvoTkuI3muIXmmbDlkozlkIzotKjljJbnq57kuonpl67popgKCiMjIO+8iOS4ie+8iSDlhb
    PplK7mioDmnK/oh6rkuLvmgKflvLHvvIznrpflipvnu4/mtY7po47pmanmlZ7lj6PmmI7mmL4KPiDlip/og73mpoLov7A6IOWIhuaekOaguOW/g+aKgOacr
    +WPl+WItuS6juS6uuWSjOS+m+W6lOmTvuWuieWFqOmjjumZqemXrumimAoKIyMg77yI5Zub77yJIOeul+WKm+iejeWQiOi1i+iDveWPl+mZkO+8jOS6p+S4
    mua4l+mAj+S9nOeUqOacieW+heWKoOW8ugo+IOWKn+iDveamgui/sDog6ZiQ6L+w566X5Yqb5LiO5Lyg57uf5Lqn5Lia6J6N5ZCI5LiN6Laz5ZKM5Lqn5Lia
    55Sf5oCB5LiN5a6M5ZaE6Zeu6aKYCgojIOWFreOAgSDmiJHlm73nrpflipvnu4/mtY7lj5HlsZXmlL/nrZblu7rorq4KPiBpc19jb3JlX3NlY3Rpb246IGZh
    bHNlCj4g5Yqf6IO95qaC6L+wOiDmj5Dlh7rkv4Pov5vkuK3lm73nrpflipvnu4/mtY7lj5HlsZXnmoTlm5vkuKrmlrnpnaLnmoTmlL/nrZblu7rorq7lkozl
    rp7mlr3ot6/lvoQKCiMjIO+8iOS4gO+8ieWujOWWhOmhtuWxguiuvuiuoe+8jOWKoOW8uuWuj+inguaUv+etluW8leWvvAo+IOWKn+iDveamgui/sDog5bu6
    6K6u5a6M5ZaE566X5Yqb57uP5rWO5pS/562W5L2T57O75ZKM5Yqg5by66aG25bGC6K6+6K6h5byV5a+8CgojIyDvvIjkuozvvInmjqLntKLokL3lnLDmqKHl
    vI/vvIzlm6DlnLDliLblrpzmjqjliqjlj5HlsZUKPiDlip/og73mpoLov7A6IOW7uuiuruaOoue0ouWcsOaWueeJueiJsuWPkeWxleaooeW8j+WSjOaOqOW5
    v+ivleeCuee7j+mqjAoKIyMg77yI5LiJ77yJ5by65YyW5oqA5pyv56CU5Y+R77yM5o+Q5Y2H6Ieq5Li75Yib5paw6IO95YqbCj4g5Yqf6IO95qaC6L+wOiDl
    u7rorq7liqDlvLrmoLjlv4PmioDmnK/noJTlj5Hlkozmj5DljYfkuqfkuJrpk77oh6rkuLvlj6/mjqfog73lipsKCiMjIO+8iOWbm++8iea3seWMluW6lOeU
    qOeJteW8le+8jOWinuW8uueul+WKm+i1i+iDveS9nOeUqAo+IOWKn+iDveamgui/sDog5bu66K6u5rex5YyW566X5Yqb5bqU55So5ZKM5p6E5bu65a6M5ZaE
    55qE5Lqn5Lia55Sf5oCB5L2T57O7
    """
    res = agent._handle_report_template(report_template)
    assert """# 一、 算力经济理论基础
> is_core_section: false
> 功能概述: 系统阐述算力经济的基本概念、政策背景、定义和内涵，为后续章节提供理论支撑""" in res

    wrong_report_template = []
    res = agent._handle_report_template(wrong_report_template)
