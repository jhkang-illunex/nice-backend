"""질의 텍스트 정규화 — 색인 텍스트(`hsk_enrich` 의 ``_clean``)와 동일 규칙.

색인(문서) 쪽은 NFKC → 구분자 공백화 → 공백 압축으로 정규화되어 있으므로,
질의도 같은 변환을 거쳐야 trigram/임베딩 유사도가 대칭이 된다. 규칙을 바꿀
때는 ``nice_ingest.pipelines.hsk_enrich.pipeline`` 의 ``_SEPS``/``_clean`` 과
반드시 함께 수정할 것.
"""

from __future__ import annotations

import re
import unicodedata

# nice_ingest.pipelines.hsk_enrich.pipeline._SEPS 와 동일 집합
_SEPS = "()（）[]［］「」｢｣ㆍᆞ∙·□\x7f\xa0"
_SEP_TABLE = str.maketrans({ch: " " for ch in _SEPS})
_MULTI_SPACE = re.compile(r" {2,}")


def normalize_query(text: str) -> str:
    """NFKC 정규화 → 구분자 공백화 → 연속 공백 압축 → 양끝 공백 제거."""
    out = unicodedata.normalize("NFKC", text)
    out = out.translate(_SEP_TABLE)
    return _MULTI_SPACE.sub(" ", out).strip()
