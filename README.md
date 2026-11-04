# 배포 및 업데이트

현재 버전은 기존 전체 Qt 화면과 Electron 분석 화면을 함께 제공하는 전환 버전이다. 모든 기능이 Electron 화면으로 이관된 상태가 아니다. NAS 실데이터 검증은 대만 PC에서 필요하다.

## 개발

Python 3.11 이상, Node.js 22 이상, Go가 필요하다. 루트에서 `pip install -r requirements.txt`, dmt-next에서 `npm ci` 후 `npm start`를 실행한다. `DMT_PYTHON`으로 Python 실행 경로를 지정할 수 있다.

`powershell -File dmt-next/build.ps1`은 Python 백엔드와 Go 프로필 저장 도구를 포함한 Windows 설치 파일을 만든다. 설치된 PC에는 별도 Python 설치가 필요하지 않다.

## 대만 PC 최초 설치

GitHub Releases의 Windows 설치 파일을 한 번 설치한다. 앱 사용자 데이터 폴더의 `.env`에 DMT_NAS_HOST, DMT_NAS_PORT, DMT_NAS_USER, DMT_NAS_PASSWORD, DMT_NAS_ROOT를 설정한다. 기존 모델 JSON은 설정 가져오기로 복사한다. Windows QSettings는 기존 DMT 설정을 읽는다. 사용자 데이터 폴더는 Electron app.getPath('userData')이며 Windows 기본 경로는 `%APPDATA%/dmt-analysis-next`이다.

프로그램 파일과 사용자 데이터는 분리된다. 새 버전 설치 시 현장 경로나 비밀번호를 배포물에서 덮어쓰지 않는다.

## 버전 배포

1. dmt-next에서 `npm version patch --no-git-tag-version`으로 package.json과 lockfile 버전을 함께 올린다.
2. 변경 사항을 커밋하고 버전과 같은 `v0.1.1` 형태의 태그를 push한다.
3. Actions가 설치 파일과 업데이트 메타데이터를 draft release에 업로드한다.
4. 테스트 후 GitHub에서 draft를 publish하면 설치된 앱이 시작 시와 6시간마다 확인하여 다운로드한다. 정상 종료 시 업데이트를 설치한다.

소스 push만으로 현장 PC가 업데이트되지는 않는다. 공개된 릴리스의 버전이 설치 버전보다 높아야 한다. Draft 및 prerelease는 자동 적용하지 않는다. 현재 설치 파일은 코드 서명 인증서가 연결되지 않았다.
