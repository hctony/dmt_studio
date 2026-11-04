# DMT Analysis Next — 작업 프로필 시제품

기존 앱의 분석 기능을 대체하지 않는 별도 시제품입니다. 모델, LOT, NAS 루트 및 자주 사용하는 로컬 폴더를 프로필로 보관합니다. 비밀번호는 저장하지 않습니다. 데이터는 운영체제 사용자 설정 폴더의 `DMT Analysis Next/profiles.json`에 저장됩니다. 기존 Python 앱과 설정을 공유하지 않습니다.

Windows PowerShell에서 실행:

```powershell
cd dmt-next/backend
go build -o dmt-backend.exe .
cd ..
npm install
npm start
```

기존 IVS, 게더링, 수율, Excel, NAS 연결 기능은 아직 이관되지 않았습니다. 실제 사용을 위해서는 각 계산 결과 및 파일 형식을 기존 앱과 비교 검증해야 합니다.

기존 `yield_model_settings.json`은 가져오기 버튼으로 사용자 데이터 폴더에 보관할 수 있습니다. 경로와 다른 필드를 그대로 보존하지만 현재 분석 기능에 적용되지는 않습니다. 다시 가져오면 이전에 가져온 사본을 덮어씁니다.
