"""Disposable browser-test server. Uses real HTTP/SQLite and an explicit AI stub.

Never starts the user's private learning site, never reads its data, and never
calls a model provider. The caller owns the temporary data directory and process.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from codesprint.generation import CompatibleProvider
from codesprint.server import make_server


class FixtureProvider:
    configured = True

    def generate(self, messages):
        supplied = json.loads(messages[-1]["content"])
        request, document = supplied["request"], supplied["document"]
        quote = next(line for line in document.splitlines() if line.startswith("volatile 可以"))
        questions = []
        for index in range(request["count"]):
            questions.append({
                "title": "volatile 的并发边界",
                "prompt": f"情境 {index + 1}：多个线程对共享 volatile 计数器执行 counter++ 时，哪项判断符合资料？",
                "type": "single", "difficulty": "应用",
                "options": [
                    {"id": "a", "text": "声明 volatile 后，多个线程累加的结果必然准确"},
                    {"id": "b", "text": "volatile 不会将读取、加一和写回变成一个原子操作"},
                    {"id": "c", "text": "counter++ 在所有 JVM 中都会由一个原子指令完成"},
                    {"id": "d", "text": "增加线程数量即可消除共享计数的并发竞争"},
                ],
                "answer": ["b"],
                "explanation": "volatile 提供变量可见性及必要的顺序关系。counter++ 包括读取、加一、写回，两个线程可能读到同一个旧值并分别写回相同结果，因此仍可能丢失更新。简单共享计数可以考虑 AtomicInteger；多个变量的不变量需要锁或整体状态设计。这是固定替身回答，用于验证草稿审核流程，不是实际模型输出。",
                "option_explanations": {
                    "a": "这把可见性错误地等同于原子性，多个线程仍可能丢失更新。",
                    "b": "三个步骤不能由 volatile 自动合并为一个原子操作，这与资料一致。",
                    "c": "Java 的复合自增不能在所有运行环境中假定为一个原子操作。",
                    "d": "增加线程数量不消除竞争，通常会增加同时读写共享变量的机会。",
                },
                "source_section": "可见性不等于原子性", "evidence_quote": quote,
                "keywords": ["volatile"], "focus": "mechanism",
            })
        return {"questions": questions}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--provider", choices=("fixture", "disabled"), default="disabled")
    args = parser.parse_args()
    provider = FixtureProvider() if args.provider == "fixture" else CompatibleProvider(api_key="", model="")
    server = make_server(port=args.port, data_dir=args.data_dir, provider=provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(json.dumps({"port": server.server_address[1], "provider": args.provider}), flush=True)
    try:
        for line in sys.stdin:
            if line.strip() == "stop":
                break
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


if __name__ == "__main__":
    main()
