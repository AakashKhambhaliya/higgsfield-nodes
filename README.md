<p align="center"><img src="assets/icon.png" width="120" alt=""></p>

# Higgsfield API Nodes for ComfyUI (unofficial)

Use every model on the [Higgsfield API](https://open.higgsfield.ai) inside ComfyUI — **one node per model,
every documented setting**, with a live price on each node, spending limits, and automatic model updates.
Bring your own Higgsfield API key; you pay Higgsfield directly.

| Video | Image |
|---|---|
| Seedance 2.0 / 2.5 · Kling 2.5 / 2.6 / 3.0 / O1 / O3 · Wan 2.6 / 2.7 / 3.0 / 3.0 Prime · Happy Horse 1.0 / 1.1 · MiniMax H3 / Hailuo 2.3 · LTX 2.5 · PixVerse 6 · Grok Imagine Video · Cinema Studio 4.0 · Genjutsu | Soul 2 · SOUL Cinema · Soul Standard · GPT Image 2.5 · Marketing Studio · Ads Studio · AI Influencer · Qwen Image 3 · Z-Image Turbo · Ideogram 4.0 · Recraft 4.1 · Grok Imagine 2.0 |

New Higgsfield models are picked up automatically — no update of this pack needed.

> **Unofficial.** Not affiliated with or endorsed by Higgsfield AI. "Higgsfield" and the model names are
> trademarks of their respective owners.

## Install

Requires **ComfyUI 0.14.0 or newer** (local, portable, Desktop, or cloud). Pick one:

**1. ComfyUI Manager (easiest)** — *Manager → Custom Nodes Manager →* search **Higgsfield API Nodes** →
**Install** → restart ComfyUI. Works in ComfyUI Desktop too.

**2. comfy-cli**
```bash
comfy node install higgsfield-nodes
```

**3. Manually (git)**
```bash
cd ComfyUI/custom_nodes
git clone https://github.com/YOUR-GITHUB-USERNAME/higgsfield-nodes
pip install -r higgsfield-nodes/requirements.txt
```
(Windows portable: use `python_embeded\python.exe -m pip install -r …`.) Restart ComfyUI.

## Add your API key

1. Create a key at [console.higgsfield.ai](https://console.higgsfield.ai) → API keys and add credit.
2. In ComfyUI: **Settings (⚙) → Higgsfield → API key → paste `KEY_ID:KEY_SECRET` → Save key.**
   The key is checked with Higgsfield, then stored on this machine only (encrypted with your Windows login on
   Windows; an owner-only file in ComfyUI's user folder elsewhere). It is never saved in workflows.

### Cloud, Docker and shared servers

Set the key as an environment variable instead — it survives restarts of ephemeral machines and nobody with
access to your ComfyUI URL can change it:

```bash
HF_KEY=YOUR_KEY_ID:YOUR_KEY_SECRET        # or HF_API_KEY_ID + HF_API_KEY_SECRET
```

RunPod / Vast / Lambda / Modal: add it to the pod or container environment. Docker:
`docker run -e HF_KEY=… …`. Colab / notebooks: `os.environ["HF_KEY"] = "…"` before starting ComfyUI.
When the variable is set, the settings panel shows the key as *managed by the server*.

Hosted services that only allow pre-approved custom nodes (for example comfy.org's Comfy Cloud) can't install
packs themselves; there, availability depends on the service.

## Use it

Add a node from *Add Node → Higgsfield → Video / Image* (or search "higgsfield", "seedance", "gpt image"…).
Each node has a **mode** dropdown; each mode shows exactly the settings that Higgsfield endpoint accepts.
Image, video and audio inputs are normal ComfyUI connections (uploaded automatically); reference lists grow
a new slot as you connect one.

Start from a template: *Workflow → Browse Templates → higgsfield-nodes*:
Seedance 2.0 text to video · Kling 3.0 image to video · Seedance 2.5 reference to video ·
Soul 2 with Soul ID character · GPT Image 2.5 product shot · Genjutsu restyle video.

### Cost safety
- **Live price badge** in each node's title bar: yellow = Higgsfield's estimate for exactly these settings
  (with your key), grey = the published price range. Right-click the node to see where it comes from.
- **run = estimate only** — asks Higgsfield for the exact price without generating (free).
- **max cost (USD)** — every generation is estimated first; above the limit nothing is sent.
- Paid submissions use an idempotency key: a retried network request can never create a second charge.
- `seed` / `variation` default to *fixed*, so re-queuing unchanged settings reuses the last result instead of
  paying again. Failed and moderated generations are not charged by Higgsfield.

### Pickers
Right-click a node → *Pick Soul 2 style… / Pick Marketing Studio preset… / Pick Restyle style… /
Pick Soul ID…* — choose from your Higgsfield lists (with previews) instead of typing IDs.
**Soul ID** nodes train your own character from photos and feed it to Soul 2 / SOUL Cinema.

### Outputs
Video nodes: `video`, `video url`, `info`. Image nodes: `images`, `image urls`, `info` (request id, settings,
price, saved paths). Results are saved to `ComfyUI/output/higgsfield/` (Higgsfield deletes them after ~7 days).

## Updates

- **New Higgsfield models and settings** — *Settings → Higgsfield → Updates* checks once a day (or press
  **Check for updates**) and downloads the new model list into your user folder; restart ComfyUI to see new
  nodes (ComfyUI Desktop shows a restart button). Prices update instantly.
- **New versions of this pack** — the same panel tells you when one is out; install it with
  *Manager → Update* or `comfy node update higgsfield-nodes`.

## Troubleshooting

Every error on a node says which step failed and what happened to your money: *nothing was submitted*,
*request … was accepted and may still finish* (check console.higgsfield.ai), or *the generation succeeded —
download it here: URL*. Full details are in the ComfyUI console (look for `higgsfield`).

| Message | Fix |
|---|---|
| No Higgsfield API key is set | Settings → Higgsfield → API key, or set `HF_KEY` |
| Higgsfield rejected the API key | Create a new key in the console; paste the whole `KEY_ID:KEY_SECRET` |
| Not enough Higgsfield credits | Top up at console.higgsfield.ai |
| concurrency limit is reached | Wait for running jobs; limits depend on your account |
| Higgsfield requires: connect … / set … | The selected mode needs that input or setting |
| Higgsfield nodes are disabled: ComfyUI … is too old | Update ComfyUI to 0.14.0+ |

## Privacy

The pack talks only to `api.higgsfield.ai` (with your key), Higgsfield's storage/CDN (uploads and results, no
key), Higgsfield's public pages (model list, no key) and `api.comfy.org` (version check, no key). No telemetry.

## Development

```bash
python -m unittest discover -s tests          # add ComfyUI to PYTHONPATH for the node tests
python tools/sync_catalog.py                  # refresh the bundled model catalog before a release
```

Release: bump `version` in `pyproject.toml` and `higgsfield_nodes/__init__.py`, update `CHANGELOG.md`, push to
`main` — GitHub Actions runs the tests and publishes to the Comfy Registry.

MIT licensed — see [LICENSE](LICENSE).
