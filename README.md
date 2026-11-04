# DMT Analysis Studio

Windows용 Electron 분석 앱입니다. 기존 10개 탭의 동작·계산 로직과 캐시를 포함한 Python/Qt 백엔드를 화면 없이 실행하며, 모든 작업 화면은 Electron 안에서 제공합니다.

## 설치와 현장 설정

[최신 릴리스](https://github.com/hctony/dmt_studio/releases/latest)의 Windows 설치 파일을 내려받으세요. 별도 Python 설치는 필요하지 않습니다.

1. NAS 연결 설정에서 주소·포트·계정·비밀번호·데이터 루트를 입력합니다.
2. 모델 설정 가져오기에서 기존 yield_model_settings.json을 선택합니다.
3. 한국어 또는 繁體中文을 선택합니다. 언어는 즉시 적용되며 다음 실행에서도 유지됩니다.
4. 필요한 Excel 템플릿은 현장 파일을 선택합니다.

설정은 `%APPDATA%/dmt-analysis-next`에 저장됩니다. 기존 Windows QSettings도 유지합니다. 비밀번호와 현장 데이터는 저장소에 올리지 않습니다.

## 기능과 검증

0.3.0부터 [LOT 테스트 기록과 스마트 입력](dmt-next/WORKFLOW-LEARNING.md)을 제공합니다. 같은 모델의 성공 LOT 3개를 기록하면 반복 경로와 입력값을 추천하고 선택한 항목을 자동입력할 수 있습니다.

[기능 연결표와 테스트 범위](dmt-next/FEATURES.md)를 참고하세요. 실제 대만 NAS 권한·데이터 형식 변형·대규모 작업은 현장에서 확인해야 합니다.

## 개발과 빌드

Python 3.11 이상과 Node.js 22 이상이 필요합니다.

```powershell
pip install -r requirements.txt
cd dmt-next
npm ci
npm start
```

저장소 루트에서 실행합니다.

```powershell
python -m unittest discover -s dmt-next -p 'test_*.py'
./dmt-next/build.ps1
```

## 새 버전 배포

1. dmt-next에서 `npm version patch --no-git-tag-version`으로 버전을 올립니다.
2. 변경 사항을 커밋하고 같은 버전의 태그(예: v0.2.1)를 push합니다.
3. GitHub Actions가 설치 파일과 latest.yml을 draft release에 업로드합니다.
4. 검증 후 해당 릴리스를 공개하면 현장 앱이 새 버전을 받습니다.

앱은 시작 시와 6시간마다 업데이트를 확인하고 다운로드합니다. 작업이 끝나고 앱을 종료하면 설치됩니다. 소스 push만으로 배포되지는 않습니다. 현장 설정은 업데이트에서 덮어쓰지 않습니다.
