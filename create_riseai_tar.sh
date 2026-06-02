#!/usr/bin/env bash
set -e

rm -rf riseai-cli
mkdir -p riseai-cli
cd riseai-cli

cat > package.json <<'EOF'
{
  "name": "riseai-cli",
  "version": "0.2.0",
  "type": "module",
  "bin": {
    "riseai": "./riseai.js"
  },
  "scripts": {
    "start": "node riseai.js",
    "link": "npm link"
  },
  "dependencies": {
    "node-fetch": "^3.3.2",
    "ajv": "^8.12.0"
  }
}
EOF

cat > riseai.js <<'EOF'
#!/usr/bin/env node
import fetch from "node-fetch";
import fs from "fs";
import path from "path";
import { execFileSync } from "child_process";
import Ajv from "ajv";

const CONFIG = {
  model: process.env.RISEAI_MODEL || "qwen3-coder:30b",
  baseUrl: process.env.RISEAI_BASE_URL || "http://localhost:11434/v1/chat/completions",
  maxToolLoops: 6,
  workspace: process.cwd(),
  forbiddenPaths: ["/etc", "/var", "/usr", "/bin", "/sbin", "/root", process.env.HOME],
};

if (
  process.cwd().startsWith("/app") &&
  process.env.RISEAI_ALLOW_APP_SOURCE !== "1"
) {
  console.error("RISEAI blocked: not permitted to operate directly inside /app source.");
  console.error("Use a sandbox copy, or set RISEAI_ALLOW_APP_SOURCE=1 only for reviewed work.");
  process.exit(2);
}

const WORKSPACE = path.resolve(CONFIG.workspace);
const AUDIT_PATH = path.join(process.env.HOME || WORKSPACE, ".riseai-audit.jsonl");

const SECRET_NAME_RE =
  /(^|\/)(\.env(\.|$)|.*credentials.*|.*\.key|.*\.pem|id_rsa|id_ed25519|.*fernet.*|.*broker.*|.*token.*|.*secret.*)/i;

const SYSTEM = `
You are RISEAI, a local coding assistant for safe project work.

Doctrine:
- You may inspect files, propose edits, write reviewable files, and run sandboxed tests.
- You may NOT place trades.
- You may NOT call broker APIs.
- You may NOT change live trading settings unless the user explicitly asks and the patch is reviewable.
- All execution authority remains outside you.
- Reply with only valid JSON matching one of the schemas.

Available tools:
1) read_file
{ "action":"read_file", "path":"relative/path" }

2) write_file
{ "action":"write_file", "path":"relative/path", "content":"full file content" }

3) run_shell
{ "action":"run_shell", "cmd":"pytest -q" }

4) final
{ "action":"final", "answer":"final response here" }

Rules:
- Output ONLY JSON.
- No markdown outside JSON.
- Prefer read_file before write_file.
- Prefer tests after edits.
- Never request broker/order actions.
- Never request reading secrets, .env files, private keys, tokens, credentials, or broker configs.
`;

function audit(action, resultSummary) {
  try {
    fs.appendFileSync(
      AUDIT_PATH,
      JSON.stringify({
        ts: new Date().toISOString(),
        cwd: process.cwd(),
        action,
        result_summary: String(resultSummary || "").slice(0, 500)
      }) + "\n"
    );
  } catch {
    // Audit failure should not crash user workflow.
  }
}

function safePath(userPath) {
  if (!userPath || userPath.includes("\0")) {
    throw new Error("Invalid path");
  }

  const full = path.resolve(WORKSPACE, userPath);

  if (full !== WORKSPACE && !full.startsWith(WORKSPACE + path.sep)) {
    throw new Error("Path escape blocked");
  }

  for (const bad of CONFIG.forbiddenPaths.filter(Boolean)) {
    const resolvedBad = path.resolve(bad);
    if (full === resolvedBad || full.startsWith(resolvedBad + path.sep)) {
      throw new Error("Forbidden path blocked");
    }
  }

  return full;
}

function assertSafeReadPath(userPath) {
  if (SECRET_NAME_RE.test(userPath)) {
    throw new Error(`Blocked secret read: ${userPath}`);
  }
}

function readFileTool(args) {
  assertSafeReadPath(args.path);
  const full = safePath(args.path);

  if (!fs.existsSync(full)) {
    return `File not found: ${args.path}`;
  }

  const stat = fs.statSync(full);
  if (!stat.isFile()) {
    return `Not a file: ${args.path}`;
  }

  return fs.readFileSync(full, "utf8").slice(0, 20000);
}

function writeFileTool(args) {
  const full = safePath(args.path);

  if (SECRET_NAME_RE.test(args.path)) {
    throw new Error(`Blocked secret write: ${args.path}`);
  }

  if (String(args.content).length > 200000) {
    throw new Error("Write exceeds size limit");
  }

  fs.mkdirSync(path.dirname(full), { recursive: true });
  fs.writeFileSync(full, args.content ?? "", "utf8");
  return `Wrote ${args.path}`;
}

function isDangerousCommand(cmd) {
  const blocked = [
    "rm -rf",
    "sudo",
    "curl ",
    "wget ",
    "ssh ",
    "scp ",
    "chmod 777",
    "mkfs",
    "dd ",
    "broker",
    "public.com",
    "kraken",
    "alpaca",
    "order",
    "place_order",
    "buy ",
    "sell ",
    "short ",
    "cover ",
    ".env",
    "credentials",
    "id_rsa",
    "id_ed25519",
    ".pem",
    ".key",
    "fernet",
    "token",
    "secret"
  ];

  const lower = String(cmd || "").toLowerCase();
  return blocked.some(x => lower.includes(x));
}

function isAllowedAutoCommand(cmd) {
  const c = String(cmd || "").trim();

  return (
    c === "npm test" ||
    c === "pytest" ||
    c === "pytest -q" ||
    c === "git status" ||
    c === "git diff" ||
    /^npm run [a-zA-Z0-9:_-]+$/.test(c) ||
    /^python -m pytest( -q)?$/.test(c) ||
    /^node [a-zA-Z0-9_./-]+\.js$/.test(c) ||
    /^ls( -la)?( [a-zA-Z0-9_./-]+)?$/.test(c) ||
    /^grep -R [a-zA-Z0-9_'"./ -]+$/.test(c)
  );
}

function runShellTool(args) {
  const cmd = args.cmd;

  if (!cmd) {
    return "Empty command";
  }

  if (isDangerousCommand(cmd)) {
    return `Blocked dangerous command: ${cmd}`;
  }

  if (process.env.RISEAI_AUTO_CONFIRM === "1" && !isAllowedAutoCommand(cmd)) {
    return `Blocked non-allowlisted auto-confirm command: ${cmd}`;
  }

  const dockerArgs = [
    "run",
    "--rm",
    "--network",
    "none",
    "--memory",
    "768m",
    "--cpus",
    "1",
    "-v",
    `${WORKSPACE}:/work`,
    "-w",
    "/work",
    "node:20",
    "bash",
    "-lc",
    cmd
  ];

  try {
    const out = execFileSync("docker", dockerArgs, {
      encoding: "utf8",
      timeout: 60000,
      maxBuffer: 1024 * 1024
    });

    return out.slice(0, 20000);
  } catch (err) {
    return String(err.stdout || err.stderr || err.message).slice(0, 20000);
  }
}

function promptConfirm(question) {
  return new Promise(resolve => {
    if (!process.stdin.isTTY) {
      console.error("Non-TTY stdin detected. Auto-declining.");
      resolve(false);
      return;
    }

    process.stdout.write(question);
    process.stdin.setEncoding("utf8");
    process.stdin.once("data", d => {
      resolve(String(d || "").trim().toLowerCase() === "y");
    });
  });
}

const ajv = new Ajv();

const schemas = {
  read: {
    type: "object",
    properties: {
      action: { const: "read_file" },
      path: { type: "string" }
    },
    required: ["action", "path"],
    additionalProperties: false
  },
  write: {
    type: "object",
    properties: {
      action: { const: "write_file" },
      path: { type: "string" },
      content: { type: "string" }
    },
    required: ["action", "path", "content"],
    additionalProperties: false
  },
  run: {
    type: "object",
    properties: {
      action: { const: "run_shell" },
      cmd: { type: "string" }
    },
    required: ["action", "cmd"],
    additionalProperties: false
  },
  final: {
    type: "object",
    properties: {
      action: { const: "final" },
      answer: { type: "string" }
    },
    required: ["action", "answer"],
    additionalProperties: false
  }
};

const validators = {
  read: ajv.compile(schemas.read),
  write: ajv.compile(schemas.write),
  run: ajv.compile(schemas.run),
  final: ajv.compile(schemas.final)
};

function validateAction(obj) {
  if (validators.read(obj)) return "read_file";
  if (validators.write(obj)) return "write_file";
  if (validators.run(obj)) return "run_shell";
  if (validators.final(obj)) return "final";

  throw new Error("Invalid action JSON");
}

async function askModel(messages) {
  const res = await fetch(CONFIG.baseUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model: CONFIG.model,
      messages,
      temperature: 0.2,
      stream: false
    })
  });

  if (!res.ok) {
    throw new Error(`Model server ${res.status}: ${await res.text()}`);
  }

  const json = await res.json();
  return json.choices?.[0]?.message?.content || "";
}

function parseJsonOnly(text) {
  try {
    return JSON.parse(text);
  } catch {
    const m = text.match(/\{[\s\S]*\}/);
    if (!m) {
      throw new Error("Model did not return JSON");
    }
    return JSON.parse(m[0]);
  }
}

async function main() {
  const args = process.argv.slice(2);
  const dry = args.includes("--dry-run") || args.includes("-n");
  const cleaned = args.filter(a => a !== "--dry-run" && a !== "-n");
  const prompt = cleaned.join(" ");

  if (!prompt) {
    console.log(`Usage:
  riseai "read package.json and explain"
  riseai --dry-run "make change X"
  riseai "run npm test and explain failures"

Requires:
  ollama serve
  ollama pull qwen3-coder:30b

Optional:
  RISEAI_MODEL=qwen3-coder:30b
  RISEAI_AUTO_CONFIRM=1
  RISEAI_ALLOW_APP_SOURCE=1
`);
    process.exit(0);
  }

  const messages = [
    { role: "system", content: SYSTEM },
    { role: "user", content: prompt }
  ];

  for (let loop = 0; loop < CONFIG.maxToolLoops; loop++) {
    const raw = await askModel(messages);

    let actionObj;
    try {
      actionObj = parseJsonOnly(raw);
    } catch (e) {
      console.error("Bad model output:");
      console.error(raw);
      throw e;
    }

    const kind = validateAction(actionObj);

    if (kind === "final") {
      audit(actionObj, "final");
      console.log(actionObj.answer);
      return;
    }

    if (dry) {
      console.log("DRY RUN: model action:");
      console.log(JSON.stringify(actionObj, null, 2));
      audit(actionObj, "dry-run");
      return;
    }

    let result;

    try {
      if (kind === "read_file") {
        result = readFileTool(actionObj);
      } else if (kind === "write_file") {
        const confirm =
          process.env.RISEAI_AUTO_CONFIRM === "1"
            ? true
            : await promptConfirm(`Write file ${actionObj.path}? (y/N) `);

        if (!confirm) {
          result = "Aborted by user";
        } else {
          result = writeFileTool(actionObj);
        }
      } else if (kind === "run_shell") {
        const autoSafePytest = actionObj.cmd.trim() === "pytest" || actionObj.cmd.trim() === "pytest -q";

        const confirm =
          autoSafePytest
            ? true
            : process.env.RISEAI_AUTO_CONFIRM === "1"
              ? true
              : await promptConfirm(`Run shell command: ${actionObj.cmd}? (y/N) `);

        if (!confirm) {
          result = "Aborted by user";
        } else {
          result = runShellTool(actionObj);
        }
      } else {
        result = "Unknown action";
      }
    } catch (err) {
      result = `Tool error: ${err.message}`;
    }

    audit(actionObj, result);

    messages.push({
      role: "assistant",
      content: JSON.stringify(actionObj)
    });

    messages.push({
      role: "user",
      content: `Tool result:\n${result}`
    });
  }

  console.log("Stopped after max loops.");
}

main().catch(err => {
  console.error(err.message);
  process.exit(1);
});
EOF

chmod +x riseai.js

cat > README.md <<'EOF'
# riseai-cli

Local self-hosted coding assistant CLI.

## Install

```bash
npm install
npm link
```

## Requires Ollama

```bash
ollama serve
ollama pull qwen3-coder:30b
```

## Usage

```bash
riseai "read package.json and explain"
riseai --dry-run "make change X"
riseai "run npm test and explain failures"
```

## Optional env vars

- `RISEAI_MODEL=qwen3-coder:30b`
- `RISEAI_BASE_URL=http://localhost:11434/v1/chat/completions`
- `RISEAI_AUTO_CONFIRM=1`
- `RISEAI_ALLOW_APP_SOURCE=1`

## Safety

- Blocks direct `/app` source operation unless `RISEAI_ALLOW_APP_SOURCE=1`
- Blocks `.env`, credentials, keys, tokens, broker-related secrets
- Shell runs inside Docker with no network
- Auto-confirm mode only allows a small command allowlist
- Writes audit receipts to `~/.riseai-audit.jsonl`
EOF

tar -czf ../riseai-cli.tar.gz .
cd ..
echo "Created riseai-cli.tar.gz"
echo "To install:"
echo "  tar -xzf riseai-cli.tar.gz -C riseai-cli-unpacked"
