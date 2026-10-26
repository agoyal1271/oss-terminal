# Shared analysis prompt

Run `node scripts/analysis-prompt.mjs GLD "Explain trend and volatility"`
from the frontend directory after `npm ci`. The runner bundles and executes
the same `loadOptionsPrompt` function used by the options page, fetching a
fresh chain and the common technical evidence pack. Its output is the
prepared prompt, not a model answer. Set `ANALYSIS_API_BASE` to override the
production API URL.

The local Slack daemon in `/Users/archit/OpenWorker/oss-terminal` invokes
this runner when Codex execution or queueing is enabled. Its optional
`ANALYSIS_PROMPT_RUNNER` and `ANALYSIS_NODE_BIN` environment settings override
the runner and Node executable paths. Codex execution posts its answer into
the originating Slack thread; queue-only mode delivers the prompt to the
configured Codex conversation. The laptop and daemon must remain running.

All Codex questions now use the options-page evidence and descriptive
analysis rules, including questions that previously selected the separate
strategy or covered-call prompts. No API key or Slack token belongs in this
directory.
