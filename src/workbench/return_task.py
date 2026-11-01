"""Conservative task routing. Quotes are user text, never authority to write."""

import re

POLICY = r"规则|政策|规定|办法|条款|时限|期限|例外|覆盖|适用|哪些货|哪些商品|照片|图片|缺陷|拆封"
DENY = r"(?:不要|别|不必|不用|暂不|暂时不|还不|不准备|不打算|暂时别).{0,8}(?:申请|办理|提交|建单|建申请|工单|流程)"
EXPLAIN = r"我想(?:了解|知道)|如何|怎么|(?:只|仅|先).{0,5}(?:讲|解释|介绍|了解|问|知道|查|核对)|解释一下|说说"
APPLY = r"(?:请|帮我|我要|我想|然后).{0,6}(?:申请|办理|提交).{0,4}退货"


def task_frame(query, requested=()):
    text = query or ""
    denials = list(re.finditer(DENY, text))
    # Remove denied operations before checking a positive, possibly mixed request.
    masked = text
    for match in reversed(denials):
        masked = masked[: match.start()] + " " * len(match.group()) + masked[match.end() :]
    positive = next(
        (m for m in re.finditer(APPLY, masked) if not re.search(r"了解|知道|怎么|如何", m.group())),
        None,
    )
    policy = re.search(POLICY, text)
    explanation = re.search(EXPLAIN, text)
    if positive:
        purpose, source = "application", positive
    elif requested and (explanation or denials) and not policy:
        purpose, source = "facts", explanation or denials[0]
    elif (policy and explanation) or denials:
        purpose, source = "policy", explanation or denials[0]
    else:
        purpose, source = "unspecified", None
    return {
        "purpose": purpose,
        "origin": "explicit_user_wording" if source else "legacy_workflow_entry",
        "quote": source.group() if source else None,
        "start": source.start() if source else None,
        "end": source.end() if source else None,
        "does_not_authorize_write": True,
    }
