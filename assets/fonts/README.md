# 보고서 글꼴

보고서 PDF에 넣을 한글 글꼴(`*.ttf`)을 이 폴더에 두면 가장 먼저 사용한다.
글꼴 파일은 저장소에 올리지 않는다(`.gitignore`).

탐색 순서: `assets/fonts/*.ttf` → `policy.yaml`의 `font_path` → `policy.yaml`의 `font_fallbacks`.
어디에서도 찾지 못하면 보고서를 만들지 않고 오류로 멈춘다.
