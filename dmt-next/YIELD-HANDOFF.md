# 수율 입력 자동 연결 / 良率輸入自動連結

IVS 스캔·JSON 조회, StripID 추출, 게더링 단일/일괄 작업이 완료되면 앱 데이터 폴더에 원본 결과를 자동 보관한다. 수율 탭에 들어갈 때 가장 최근에 완료한 모델·LOT의 데이터를 채운다. VRS/FVI/IVS TXT와 IVS JSON, StripID JSON, BBT/Skipdata, 최종 SKI, X×Y Unit 수, 허용수(X-out)를 연결한다. 별도 파일 내보내기는 필요 없다.

모델·LOT별로 분리하며 다른 LOT 전환 시 이전 경로를 비운다. 완료하지 않은 단계는 빈칸으로 남는다. IVS 불량 목록이나 게더링 제외 StripID 목록을 전체 StripID 목록으로 사용하지 않는다. 전체 StripID JSON은 StripID 추출 완료 결과를 사용한다. 취소한 검수, 중단·실패한 작업은 연결하지 않는다.

새 완료 결과가 없으면 사용자가 수정한 값을 유지한다. `최근 완료 데이터 다시 불러오기`로 마지막 완료 값을 명시적으로 다시 적용할 수 있다. 완료 결과와 연결 기록은 업데이트 후에도 유지되는 `userData/yield-handoff/`에 저장하며 공개 저장소에 포함하지 않는다. 이 버전 이전에 내보내지 않은 메모리 결과는 복원할 수 없으므로 업데이트 후 한 번 실행한 작업부터 자동 연결한다.

進入良率畫面時，自動帶入最近完成之型號及 LOT 的製程 TXT、JSON、BBT、SKI、Unit 數與 X-out。不同 LOT 的路徑分開保存。尚未完成的項目保持空白；沒有新結果時保留手動修改。需要時可按「重新載入最近完成的資料」。完整 StripID 清單取自 StripID 擷取結果，不能以不良或排除清單代替。

Validation: Python workflow tests cover three-process extraction → Yield tab navigation, original JSON callbacks, model/LOT isolation, immutable rerun snapshots, manual edits, persistence and cancelled/stopped work. `smoke-yield-handoff.cjs` runs the actual Electron renderer against the source backend and checks autofill and Traditional Chinese labels.
