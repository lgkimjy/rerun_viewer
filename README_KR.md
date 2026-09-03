# MuJoCo H5 → Rerun Viewer

[English](README.md) | **한국어**

`stateData.h5`의 시계열과 MuJoCo scene을 하나의 Rerun Viewer에서 동기화해 보는 도구다. Linux와 macOS에서 같은 명령을 사용한다.

## 설치

```bash
conda env create -f scripts/rerun/environment.yml
conda activate mujoco-rerun
```

이미 환경을 만들었다면 업데이트한다.

```bash
conda env update -f scripts/rerun/environment.yml --prune
conda activate mujoco-rerun
```

## 기본 사용법

일반적으로 필요한 명령은 `view_h5.py` 하나뿐이다.

```bash
python3 scripts/rerun/view_h5.py \
  logs/20260901_152933/stateData.h5 \
  --model model/template/scene.xml
```

이 명령은 H5 시계열 변환, `mj_forward`를 이용한 scene 복원, RRD 병합 및 Web Viewer 실행을 모두 처리한다. `<timestamp>` 같은 표기는 실제 폴더명으로 바꿔야 하는 placeholder다.

H5 경로를 생략하면 `logs/**/stateData.h5` 중 수정 시간이 가장 최근인 파일을 자동으로 선택한다.

```bash
python3 scripts/rerun/view_h5.py --model model/template/scene.xml
```

선택된 파일은 `using latest H5: ...` 형식으로 실행 전에 출력된다.

### Native Viewer

로컬에서 큰 로그를 자주 확인한다면 Native Viewer를 권장한다.

```bash
python3 scripts/rerun/view_h5.py \
  logs/20260901_152933/stateData.h5 \
  --model model/template/scene.xml \
  --native
```



### RRD만 생성

```bash
python3 scripts/rerun/view_h5.py \
  logs/20260901_152933/stateData.h5 \
  --model model/template/scene.xml \
  --no-view
```



### 주요 옵션

```text
--layout PATH          사용할 Blueprint YAML
--stride N             N개 중 1개 frame만 기록
--native               Native Viewer 사용
--no-view              파일만 생성
--rebuild-scene        MuJoCo scene 캐시 강제 재생성
--web-port PORT        Web Viewer 포트 (기본 9090)
```

`layout.yaml`을 수정한 뒤에는 Viewer를 종료하고 `view_h5.py`만 다시 실행하면 된다. Plot과 Blueprint는 항상 갱신되고, H5와 MJCF가 변하지 않았다면 scene RRD는 캐시를 재사용한다.

`--rebuild-scene`은 include된 XML, mesh 또는 texture가 바뀌었지만 최상위 scene XML의 수정 시간이 바뀌지 않은 경우에 사용한다.

## 출력 파일

```text
stateData.rrd                  시계열 + Blueprint
stateData_mujoco.rrd           MuJoCo scene과 geom transform
stateData_mujoco.rrd.stride    scene 캐시의 stride 정보
stateData_combined.rrd         Viewer에서 여는 최종 통합 파일
```

기존 RRD만 다시 열려면 Conda 환경의 Rerun CLI를 직접 사용한다.

```bash
# Native
rerun logs/20260901_152933/stateData_combined.rrd

# Web
rerun --web-viewer logs/20260901_152933/stateData_combined.rrd
```



## Plot과 스타일 설정

기본 설정은 `scripts/rerun/layout.yaml`이다. 같은 `series` 목록에 있는 dataset은 하나의 TimeSeries View에 겹쳐 표시된다.

```yaml
plots:
  - name: Base linear velocity — feedback vs command
    series:
      - fbk/pdot_B
      - ctrl/lin_vel_d
```

H5에 없는 series는 제외된다. 모든 series가 없다면 빈 subplot을 만들지 않고 plot 자체를 자동 생략한다.

vector에서 일부 component만 표시하려면 mapping 형식을 사용한다. index는 0부터 시작하고 legend에도 원래 번호가 유지된다.

```yaml
plots:
  - name: Joint position — feedback vs command
    series:
      - path: fbk/jpos
        indices: [0, 5, 10]
        name: [right knee, right ankle, right hip]
      - path: ctrl/jpos_d
        indices: [0, 5, 10]
        name: [right knee, right ankle, right hip]
```

`name`은 선택 사항이며 선택한 index마다 이름 하나를 지정해야 한다. Rerun에는 `fbk.right knee`, `ctrl.right knee`처럼 dataset role이 앞에 붙는다. 기존 문자열 형식은 모든 component를 표시한다.

Error series는 변환 중 계산되어 RRD에만 저장되고 H5에는 추가되지 않는다. 예:

```yaml
err:
  - path: err/jpos
    operation: subtract
    lhs: ctrl/jpos_d
    rhs: fbk/jpos
```

위 설정은 `err/jpos = ctrl/jpos_d - fbk/jpos`를 만들며 일반 series처럼 `plots`에서 사용할 수 있다. 현재 지원하는 연산은 `subtract`이다.

기본 스타일:

- component 0/1/2 또는 x/y/z: 빨강/초록/파랑
- `fbk/*`: 굵은 실선
- `ctrl/*`: 같은 component 색상의 point marker
- `param/*`: 얇은 실선

색상, 굵기 및 marker 크기는 `layout.yaml`에서 수정한다. Rerun 0.37의 `SeriesLines`는 dashed line pattern을 지원하지 않는다.

## CoM 표시

초기 3D 카메라는 world 좌표로 설정할 수 있다. `position`은 카메라 위치이고 `look_target`은 바라보는 점이며, Viewer가 열린 뒤에는 마우스로 자유롭게 조작할 수 있다.

```yaml
scene:
  initial_view:
    enabled: true
    position: [3.5, -3.5, 2.2]
    look_target: [0.0, 0.0, 1.0]
```

`layout.yaml`의 `scene.com` 항목에서 H5 dataset을 CoM sphere에 직접 연결한다.

```yaml
scene:
  com:
    enabled: true
    dataset: fbk/p_CoM
    radius: 0.035
    color: [255, 210, 30]
```

dataset은 sample마다 하나의 3-vector를 가져야 한다. 이 값은 MuJoCo로 다시 계산하지 않고 그대로 사용한다. 숨기려면 `enabled: false`로 설정한다.

Contact 위치와 힘도 H5에서 화살표로 직접 연결할 수 있다.

```yaml
scene:
  hidden_geoms:
    - ground

  contact_forces:
    - name: actual
      positions: fbk/contact_positions
      vectors: fbk/contact_forces
      count: fbk/contact_count
      scale: 0.002
      radius: 0.008
      color: [255, 80, 40]

    - name: desired
      positions: ctrl/contact_positions_d
      vectors: ctrl/contact_forces_d
      count: ctrl/contact_count
      scale: 0.002
      radius: 0.008
      color: [50, 140, 255]
```

각 항목은 `/world/contact_forces/<name>` entity로 기록된다. `positions`와 `vectors`는 world 좌표계의 `(T, N, 3)` 또는 펼친 `(T, 3*N)` dataset을 받는다. 펼친 row는 `[x0, y0, z0, x1, y1, z1, ...]` 순서여야 한다. 모든 `N`개가 유효하면 `count`는 생략할 수 있다. `scale`은 화살표 길이만 조절한다. `hidden_geoms`는 MuJoCo 모델을 변경하지 않고 Rerun 표시에서만 해당 geom을 제외하며, 대소문자를 구분하지 않는 glob을 지원한다.

## H5 구조 확인

```bash
python3 scripts/rerun/inspect_h5.py logs/20260901_152933/stateData.h5
```

Scene 복원에는 `time`과 `fbk/qpos`가 필요하며, `fbk/qpos.shape[1]`은 MJCF의 `model.nq`와 같아야 한다.

## 파일 구성

직접 사용하는 파일:

```text
view_h5.py       전체 변환·병합·Viewer 실행
inspect_h5.py    H5 구조 검사
layout.yaml      Plot, scene 및 스타일 설정
environment.yml Conda 환경
```

내부 구현:

```text
rerun_tools.py    H5 변환, Blueprint, MuJoCo scene 및 RRD 병합
```



## 현재 제한 사항

현재 C++ `HDF5Logger`는 SWMR write mode를 사용하지 않는다. 따라서 기록 중인 H5를 안전하게 읽는 live tail 기능은 제공하지 않는다. 시뮬레이션이 H5를 닫은 뒤 `view_h5.py`를 실행해야 한다.
