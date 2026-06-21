# Identity-preserving GPT-Image-2 generation via Codex

Use this when the user sends a face/photo reference and says the generated person must be the same person / “1 в 1 лицо” / “это я”.

## Lesson

Do **not** rely on text-only trait descriptions for identity-critical work. Text prompts preserve a type, not a face. If the tool path only sends `prompt` + `aspect_ratio`, the model will drift into a generic actor.

For identity-critical edits/generations, send the original image as an actual `input_image` to GPT-Image-2 through the Codex Responses endpoint, plus a short explicit prompt that says identity match is the top priority.

## Minimal runtime pattern

Use Codex OAuth token + Cloudflare-compatible headers from Hermes internals:

```python
import base64, os, sys
from pathlib import Path
from openai import OpenAI
sys.path.insert(0, os.environ.get('HERMES_AGENT_DIR', '<hermes-agent-install>'))
from agent.auxiliary_client import _read_codex_access_token, _codex_cloudflare_headers

token = _read_codex_access_token()
ref = Path('/path/to/reference.jpg')
data_url = 'data:image/jpeg;base64,' + base64.b64encode(ref.read_bytes()).decode('ascii')

client = OpenAI(
    api_key=token,
    base_url='https://chatgpt.com/backend-api/codex',
    default_headers=_codex_cloudflare_headers(token),
    timeout=300,
)

prompt = '''Use the attached portrait photo as the exact identity reference.
Preserve the person's face 1:1. Do not invent a new face, do not change hairstyle,
age, eyes, jaw, expression, stubble, or distinctive accessories.
Create: ...desired scene...'''

kwargs = dict(
    model='gpt-5.5',
    instructions='Generate the requested image using the image_generation tool. Preserve identity from the attached reference photo.',
    input=[{
        'role': 'user',
        'content': [
            {'type': 'input_text', 'text': prompt},
            {'type': 'input_image', 'image_url': data_url},
        ],
    }],
    tools=[{
        'type': 'image_generation',
        'size': '1536x1024',
        'quality': 'high',
        'output_format': 'png',
    }],
    store=False,
)

collected = []
with client.responses.stream(**kwargs) as stream:
    for event in stream:
        if getattr(event, 'type', '') == 'response.output_item.done':
            item = getattr(event, 'item', None)
            if item is not None:
                collected.append(item)
    final = stream.get_final_response()

items = collected + list(getattr(final, 'output', []) or [])
for item in items:
    d = item.model_dump() if hasattr(item, 'model_dump') else item
    if isinstance(d, dict) and d.get('type') == 'image_generation_call':
        result_b64 = d.get('result')
        if result_b64:
            Path('/tmp/out.png').write_bytes(base64.b64decode(result_b64))
            break
```

## Prompt checklist

- Start with: “Use the attached portrait photo as the exact identity reference.”
- Say: “Preserve the person's face 1:1. Do not invent a new face.”
- List only identity-critical traits, not a long character description.
- Put scene/style/action after identity instructions.
- For exact text in the image, quote it plainly and run vision QA before delivery.

## QA

Before delivery, use image understanding to check:
- face is not obviously a generic/new person;
- distinctive hairstyle/eyes/stubble/accessories survived;
- requested exact text is readable;
- unwanted elements from prior failed generations are absent.

If the user says “not me / not the same face”, treat it as workflow failure and switch immediately to this input-image path.
