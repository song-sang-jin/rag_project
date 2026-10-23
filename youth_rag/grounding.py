"""명시적으로 요청한 공고 연도·차수가 실제 인용 근거에 있는지 검사한다."""
import re


def document_scope(title):
    year = re.search(r"(?<!\d)(20\d{2})(?!\d)", title)
    batch = re.search(r"(?:제\s*)?(\d{1,2})\s*차", title)
    return (year.group(1) if year else None, batch.group(1) if batch else None)


def requested_scopes(question, policy_title):
    # 서류 발급월·생년 등은 공고 연도로 오인하지 않는다.
    patterns = [r"(20\d{2})\s*(?:년도?|년)?\s*(?:의\s*)?(?:(?:제\s*)?(\d{1,2})\s*차\s*)?(?:공고|모집|사업|정책)",
                r"(20\d{2})\s*(?:년도?|년)?\s*(?:제\s*)?(\d{1,2})\s*차"]
    scopes = []
    for pattern in patterns:
        for match in re.finditer(pattern, question):
            scope = (match.group(1), match.group(2))
            if scope not in scopes:
                scopes.append(scope)
    if not scopes:
        year, _ = document_scope(policy_title)
        for batch in re.findall(r"(?:제\s*)?(\d{1,2})\s*차\s*(?:공고|모집)", question):
            if (year, batch) not in scopes:
                scopes.append((year, batch))
    return scopes


def matches_scope(row, scope):
    year, batch = document_scope(row["meta"].get("title", ""))
    return (scope[0] is None or year == scope[0]) and (scope[1] is None or batch == scope[1])


def missing_scopes(scopes, rows):
    return [scope for scope in scopes if not any(matches_scope(row, scope) for row in rows)]


def unavailable(scopes):
    labels = [" ".join(part for part in [(y + "년") if y else "", (b + "차") if b else ""] if part)
              for y, b in scopes]
    return "문서에서 확인되지 않습니다. 요청하신 " + ", ".join(labels) + " 공고에 맞는 근거를 확인하지 못했습니다. 해당 연도·차수의 공식 공고 확인이 필요합니다."
