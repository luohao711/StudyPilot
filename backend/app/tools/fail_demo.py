"""调用失败处理演示：分别触发鉴权失败、超时和服务地址不可达三种情况。

用法：python3 -m app.tools.fail_demo
"""

from app.errors import BusinessError
from app.service import llm_client

MESSAGES = [{"role": "user", "content": "只回答两个字：可用"}]


def run(title: str, **kwargs) -> None:
    print(f"【{title}】")
    try:
        result = llm_client.chat(MESSAGES, max_tokens=10, **kwargs)
        print(f"  调用成功（未按预期失败）：{result['content'][:30]}")
    except BusinessError as exc:
        print(f"  错误码：{exc.code}")
        print(f"  错误信息：{exc.message}")
        print(f"  接口返回：HTTP {exc.status_code}")
    print("")


def main() -> None:
    print(f"模型服务：{llm_client.DEFAULT_BASE_URL}    模型：{llm_client.DEFAULT_MODEL}")
    print(f"密钥：{llm_client.mask(llm_client.api_key()) if llm_client.is_available() else '未配置'}")
    print("-" * 78)
    run("情况1：API Key 无效", api_key_override="sk-invalid-key-for-demo")
    run("情况2：超时（超时时间设为 1 毫秒）", timeout=0.001)
    run("情况3：服务地址不可达", base_url="http://127.0.0.1:9")


if __name__ == "__main__":
    main()
