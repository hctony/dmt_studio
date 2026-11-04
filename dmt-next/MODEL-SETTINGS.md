# 기존 Python Studio 설정 이어받기 / 沿用舊版設定

왼쪽 아래 **모델 설정 / 캐시 → 기존 JSON 가져오기**에서 Python Studio의
`yield_model_settings.json` 또는 같은 내용의 `.txt`를 선택합니다.
모델 목록에서 모델을 고른 뒤 **선택 모델 적용**으로 수율 화면에 불러옵니다.

繁體中文：左下角 **機種設定 / 快取 → 匯入舊版 JSON**，選取 Python Studio 的
`yield_model_settings.json`（或內容相同的 `.txt`），選擇機種後按 **套用所選機種**。

지원 항목 / 支援欄位:
- `units_per_strip`: Strip당 수량 / 每片 Strip 數量
- `xout_limit`: X-out 허용수 / X-out 容許數
- `vrs_txt_paths`, `fvi_txt_paths`, `ivs_txt_paths`, `ivs_json_paths`: 공정별 입력 경로 / 各製程輸入路徑
- `skipdata_path`, `ski_path`: BBT/Skipdata 폴더와 최종 SKI / BBT/Skipdata 資料夾及最終 SKI

같은 모델의 제공된 필드만 갱신하며 나머지 모델과 항목은 보존합니다.
빈 문자열은 해당 항목을 비웁니다. 백업은 설정 폴더의 `settings-backups/<UTC 시각>`에
저장합니다. 가져온 모델과 화면 입력은 재시작 및 앱 업데이트 뒤에도 유지됩니다.

匯入僅更新相同機種中提供的欄位，保留其他機種與欄位。空字串會清空該欄位。
原有設定備份於設定資料夾的 `settings-backups/<UTC 時間>`。
機種設定及畫面輸入會在重新啟動及更新後保留。

Gathering 적용 선택 시 모델명·허용수·Skipdata·SKI만 함께 반영합니다.
X/Y 좌표 수는 총 수량만으로 추론하지 않습니다. 경로의 LOT는 원문 그대로이므로
실행 전에 실제 LOT·파일을 확인하세요. 대만 PC의 경로가 여기서 접근되지 않아도
가져오기는 가능하며 분석을 자동 실행하지 않습니다.

勾選 Gathering 時，只同步機種、容許數、Skipdata、SKI；不會從總數量推算 X/Y 座標數。
路徑中的 LOT 保持原樣，執行前請確認實際 LOT 與檔案。
即使此電腦無法存取台灣 PC 的路徑，仍可匯入；不會自動執行分析。

이 JSON에는 NAS 로그인 정보, 다른 화면의 일반 설정, 이미 계산된 메모리 캐시가 없습니다.
기존 Python의 QSettings는 같은 Windows 사용자에서 읽지만 다른 PC로 자동 전송되지는 않습니다.
모델 JSON과 백업은 각 PC에만 저장되고 GitHub 공개 저장소나 설치 파일에 포함하지 않습니다.

此 JSON 不含 NAS 登入資訊、其他畫面的一般設定及已計算的記憶體快取。
同一 Windows 使用者可讀取舊版 QSettings，但不會自動傳送至其他電腦。
機種 JSON 與備份僅儲存在各自電腦，不包含在公開 GitHub 儲存庫或安裝檔中。
