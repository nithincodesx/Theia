import json
import os
import http.client


class LLM:
    def __init__(self, base_url="https://api.inceptionlabs.ai/v1", model="mercury-3", system="", temperature=0.8, api_key=None, max_tokens=180):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.system = system
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.api_key = api_key or os.environ.get("INCEPTION_API_KEY")
        if not self.api_key:
            raise RuntimeError("INCEPTION_API_KEY not set. Add it to .env or set the env var.")
        parsed = http.client.urlsplit(self.base_url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        self._conn = http.client.HTTPSConnection(parsed.hostname, port, timeout=30) if parsed.scheme == "https" else http.client.HTTPConnection(parsed.hostname, port, timeout=30)

    def _post(self, body):
        path = f"{self.base_url}/chat/completions"
        payload = json.dumps(body).encode()
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "Connection": "keep-alive",
        }
        try:
            self._conn.request("POST", path, body=payload, headers=headers)
            resp = self._conn.getresponse()
            raw = resp.read()
            if resp.status != 200:
                detail = raw.decode(errors="replace")[:300]
                raise RuntimeError(f"Inception API error {resp.status}: {detail}")
            return json.loads(raw)
        except Exception as exc:
            if isinstance(exc, RuntimeError):
                raise
            try:
                self._conn.close()
            except Exception:
                pass
            parsed = http.client.urlsplit(self.base_url)
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            self._conn = http.client.HTTPSConnection(parsed.hostname, port, timeout=30) if parsed.scheme == "https" else http.client.HTTPConnection(parsed.hostname, port, timeout=30)
            raise RuntimeError(f"Could not reach Inception API: {exc}") from exc

    def correct_transcript(self, text, max_tokens=120):
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": (
                    "You are a speech-to-text correction assistant. Fix transcription "
                    "errors, homophones, and artifacts in the user's dictated command, "
                    "preserving their intent (e.g. play, open, close, search, type, "
                    "app names, song and artist names). The user may speak Indian "
                    "language song and artist names in transliteration (Tamil, "
                    "Malayalam, Hindi, Telugu, Kannada). Keep those words exactly as "
                    "transliterated — do NOT translate them into English, do NOT "
                    "replace them with English words. Reply with only the corrected "
                    "text — no explanations, no quotes."
                )},
                {"role": "user", "content": text},
            ],
            "temperature": 0,
            "max_tokens": max_tokens,
            "stream": False,
        }
        out = self._post(body)
        content = out["choices"][0]["message"].get("content") or ""
        return content.strip()

    def understand_command(self, text, max_tokens=200):
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": (
                    "You convert a user's spoken request into one canonical command phrase. "
                    "Rules: (1) If the request is a command, reply with JSON {\"relay\": \"...\"} "
                    "where relay is the cleanest imperative form using exactly one of these "
                    "prefixes: play, pause, resume, stop, open, close, launch, start, search, "
                    "look up, find, type, press, scroll, click, switch to, change, turn, "
                    "minimize, maximize, show, hide, set, "
                    "ask, watch, listen to. Include the target (song/app/website/window). "
                    "(2) If the request is a question or conversation, reply with JSON "
                    "{\"relay\": \"\", \"answer\": \"...\"} — a short answer. "
                    "(3) Never invent targets. Never include shell code. Reply ONLY with JSON."
                )},
                {"role": "user", "content": text},
            ],
            "temperature": 0,
            "max_tokens": max_tokens,
            "stream": False,
        }
        out = self._post(body)
        content = out["choices"][0]["message"].get("content") or ""
        return self._parse_command_json(content)

    @staticmethod
    def _parse_command_json(content):
        content = content.strip()
        if content.startswith("```"):
            content = content.strip("`")
            if content.startswith("json"):
                content = content[4:]
        try:
            obj = json.loads(content)
        except Exception:
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    try:
                        obj = json.loads(line)
                        break
                    except Exception:
                        continue
            else:
                return {"relay": "", "answer": content}
        if not isinstance(obj, dict):
            return {"relay": "", "answer": content}
        relay = str(obj.get("relay") or "").strip()
        answer = str(obj.get("answer") or "").strip()
        return {"relay": relay, "answer": answer}

    def chat(self, messages):
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": self.system}] + messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        out = self._post(body)
        return out["choices"][0]["message"]["content"]

    def chat_with_tools(self, messages, tools, executor, max_rounds=8):
        msgs = [{"role": "system", "content": self.system}] + list(messages)
        body = {
            "model": self.model,
            "messages": msgs,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
            "tools": tools,
        }
        for _ in range(max_rounds):
            out = self._post(body)
            msg = out["choices"][0]["message"]
            tool_calls = msg.get("tool_calls")
            if not tool_calls:
                return msg.get("content") or ""
            msgs.append({
                "role": "assistant",
                "content": msg.get("content") or "",
                "tool_calls": tool_calls,
            })
            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    args = {}
                result = executor(name, args)
                msgs.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": json.dumps(result, ensure_ascii=False),
                })
        return msgs[-1].get("content", "")
