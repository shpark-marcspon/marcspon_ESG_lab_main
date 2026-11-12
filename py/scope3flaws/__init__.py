"""scope3flaws — Scope 3 1차 엔진(colab_157) 흠 회귀 묶음(EBS2). README·CASES.md.

esg-app 가상환경에는 lxml 이 있어 openpyxl 이 수식 칸을 `<f>…</f><v></v>` 로 쓴다.
v2.2 짓개(`scope3colab.synthetic_v22.write_cached_values`)의 정규식은 esg-lab 가상환경(lxml 없음)의 꼴
`<v />` 만 잡아 짓기가 실패한다. 그래서 이 패키지를 처음 import 할 때(openpyxl 보다 먼저 —
시험은 `tests/conftest.py` 가 이 패키지를 먼저 부른다) openpyxl 의 lxml 쓰기를 끈다
(`openpyxl.xml.lxml_env_set` 이 읽는 환경 변수). 엔진 읽개(`tools.xlsx_reader`)가 읽는 값에는 상관없다.
확인은 `build._check_writer`.
"""

import os

os.environ["OPENPYXL_LXML"] = "False"
