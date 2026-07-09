# 런북 — 베어본 서버에서 Docker GPU 사용 환경 설정 (LLM 추론용)

> OS만 깔린 서버에서 **컨테이너가 NVIDIA GPU를 쓰게** 하는 절차. LLM(qwen3:14b, q4_K_M 9.3GB)은
> GPU가 사실상 필수(16GB GPU 권장). 임베딩(bge-m3)·rag·shock 은 CPU라 이 설정 불필요.
> 기준: **Ubuntu 22.04** 및 **Rocky Linux 9 (RHEL 계열)** + NVIDIA GPU. 각 단계에 apt/dnf 병기.
> (설치 대상이 Rocky 이면 dnf 블록 + §7-B Rocky 특이사항(SELinux·Secure Boot) 을 따를 것.)

## 구조 — 3층이 다 있어야 컨테이너가 GPU를 본다
```
① NVIDIA 드라이버(호스트 커널)  →  ② Docker  →  ③ NVIDIA Container Toolkit(런타임 브리지)
```
셋 중 하나만 빠져도 `--gpus` 가 "could not select device driver" 로 실패한다.

---

## 1. GPU·드라이버 확인
```bash
lspci | grep -i nvidia            # GPU 인식 확인 (안 나오면 하드웨어/BIOS)
nvidia-smi                        # 드라이버 이미 있으면 표 출력, 없으면 'command not found'
```

## 2. NVIDIA 드라이버 설치 (nvidia-smi 안 될 때만)

**Ubuntu (apt):**
```bash
sudo apt update
sudo ubuntu-drivers autoinstall            # 권장(자동 매칭). 또는 특정 버전:
# sudo apt install -y nvidia-driver-550-server
sudo reboot
nvidia-smi                                 # Driver Version / CUDA Version 표시되면 성공
```

**Rocky Linux 9 / RHEL 9 (dnf):** — CUDA repo 의 dkms 드라이버 사용
```bash
# 커널 모듈 빌드 전제(dkms). 반드시 실행 중인 커널과 버전이 맞아야 함
sudo dnf install -y epel-release
sudo dnf install -y kernel-devel-$(uname -r) kernel-headers-$(uname -r) gcc make dkms
# NVIDIA CUDA 저장소 등록 (Rocky 8 이면 rhel8 로 치환)
sudo dnf config-manager --add-repo \
  https://developer.download.nvidia.com/compute/cuda/repos/rhel9/x86_64/cuda-rhel9.repo
sudo dnf clean all
sudo dnf module install -y nvidia-driver:latest-dkms    # 또는: dnf install -y nvidia-driver cuda-drivers
sudo reboot
nvidia-smi
```
> ollama/CUDA 12.x 를 위해 **드라이버 535 이상** 권장. 드라이버는 커널 모듈이라 커널 업데이트
> 후 재빌드가 필요할 수 있다(DKMS). Ubuntu 서버는 `-server` 계열 권장. **Rocky 는 Secure Boot
> 켜져 있으면 미서명 모듈이 로드 안 됨 → §7-B 참고.**

## 3. Docker 설치 (없을 때)

**Ubuntu:** `curl -fsSL https://get.docker.com | sudo sh` (get.docker.com 스크립트가 apt/dnf 자동 처리)

**Rocky Linux 9 / RHEL 9 (dnf):** — 기본 저장소에 docker 없음, Docker CE repo 추가
```bash
sudo dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo systemctl enable --now docker
```
**공통(설치 후):**
```bash
sudo usermod -aG docker $USER && newgrp docker   # sudo 없이 docker 쓰려면(재로그인)
docker run --rm hello-world
```

## 4. NVIDIA Container Toolkit 설치·구성 ★ 핵심
호스트 드라이버를 컨테이너에 연결하는 런타임. **이게 없으면 GPU 컨테이너 불가.**

**Ubuntu (apt):**
```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt update && sudo apt install -y nvidia-container-toolkit
```

**Rocky Linux 9 / RHEL 9 (dnf):** — 동일 toolkit, rpm 저장소만 다름
```bash
curl -s -L https://nvidia.github.io/libnvidia-container/stable/rpm/nvidia-container-toolkit.repo \
  | sudo tee /etc/yum.repos.d/nvidia-container-toolkit.repo
sudo dnf install -y nvidia-container-toolkit
```

**공통(설치 후) — Docker 데몬에 nvidia 런타임 등록:**
```bash
sudo nvidia-ctk runtime configure --runtime=docker    # /etc/docker/daemon.json 자동 수정
sudo systemctl restart docker
```

## 5. 검증 (필수)
```bash
docker info | grep -i runtime                      # 'nvidia' 런타임 보여야
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
#   → 컨테이너 안에서 GPU 표가 뜨면 성공. (cuda 이미지는 검증용, 없으면 pull 필요)
```
> 에어갭이면 검증용으로 `ollama/ollama` 이미지로 대체: `docker run --rm --gpus all ollama/ollama nvidia-smi`.

---

## 6. 이 프로젝트 LLM(ollama + qwen3:14b)을 GPU로 실행

ollama 는 toolkit 만 깔려 있으면 GPU 를 **자동 감지**한다. `--gpus` 만 붙이면 된다.

### 방법 A) docker run (단독)
```bash
docker run -d --name nice-llm --gpus all \
  -v nice-backend_llm-models:/root/.ollama \
  -p 11434:11434 ollama/ollama:latest
# 모델 확인 (번들 복원돼 있으면 qwen3:14b 보임)
docker exec nice-llm ollama list
```

### 방법 B) docker compose — ollama 서비스에 GPU 블록 override
`docker-compose.gpu.yml` 은 **vLLM 용**이다. ollama 로 GPU 를 쓰려면 아래 override 파일을 쓴다.
`docker-compose.gpu-ollama.yml`:
```yaml
services:
  llm:
    deploy:
      resources:
        reservations:
          devices:
            - {driver: nvidia, count: 1, capabilities: [gpu]}
```
```bash
docker compose -f docker-compose.yml -f docker-compose.gpu-ollama.yml \
  --profile llm-local up -d llm
```

### GPU 사용 확인
```bash
docker logs nice-llm 2>&1 | grep -i "gpu\|cuda\|offload"   # 'offloaded ... layers to GPU'
nvidia-smi                                                  # ollama 프로세스가 VRAM 점유
```
> qwen3:14b q4_K_M(9.3GB) + KV캐시 → **16GB VRAM** 이면 전 레이어 GPU offload. VRAM 부족 시
> ollama 가 일부만 GPU/나머지 CPU 로 분할(느림) → `nvidia-smi` VRAM·`offloaded N/M layers` 로 확인.

---

## 7-A. 에어갭(인터넷 차단) 주의

§2·§4 의 설치는 인터넷이 필요하다. 차단 환경이면 **연결 구간(동일 OS·커널)에서 패키지를 미리 받아** 반입:

**Ubuntu (.deb):**
```bash
# (연결 구간) 드라이버 + toolkit .deb 및 의존성까지
sudo apt install --download-only -y nvidia-driver-550-server nvidia-container-toolkit
cp /var/cache/apt/archives/*.deb ./gpu-pkgs/       # 매체로 이동
# (대상 서버)
sudo dpkg -i ./gpu-pkgs/*.deb || sudo apt install -f
```

**Rocky/RHEL (.rpm):**
```bash
# (연결 구간) 의존성 포함 전부 받기
sudo dnf install -y epel-release
sudo dnf download --resolve --alldeps --destdir=./gpu-pkgs \
  kernel-devel-$(uname -r) dkms nvidia-driver cuda-drivers nvidia-container-toolkit
# (대상 서버)
sudo dnf install -y ./gpu-pkgs/*.rpm       # localinstall, 저장소 없이 로컬 rpm 해석
```
**공통(설치 후):**
```bash
sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker
```
> 드라이버 커널 모듈은 **대상 서버 커널 버전과 정확히 맞아야** 한다. 대상과 동일 커널의 연결
> 구간에서 받거나, `.run` 러너(NVIDIA-Linux-x86_64-*.run)를 반입해 로컬 빌드하는 방법도 있다.

## 7-B. Rocky/RHEL 특이사항 (Ubuntu 엔 없는 함정)

1. **SELinux (enforcing 기본)** — 컨테이너 GPU/볼륨 접근을 막을 수 있다. nvidia-container-toolkit
   이 SELinux 정책을 대체로 처리하지만, `permission denied`/`device` 오류가 나면:
   ```bash
   getenforce                                   # Enforcing 이면 후보 원인
   # 볼륨 마운트 라벨 문제면 -v ...:Z 또는 임시로:
   docker run --gpus all --security-opt label=disable ...
   ```
2. **Secure Boot** — 켜져 있으면 **미서명 NVIDIA 커널 모듈이 로드 실패**(nvidia-smi 안 됨).
   `mokutil --sb-state` 로 확인 → BIOS 에서 끄거나 MOK 로 모듈 서명. 대개 서버는 끄는 게 간단.
3. **kernel-devel 버전 불일치** — dkms 빌드 실패의 최다 원인. `uname -r` 과
   `rpm -q kernel-devel` 이 **정확히 같아야** 한다. 다르면 커널을 맞추거나 헤더를 그 버전으로 설치.
4. **firewalld** — 호스트 방화벽이 서비스 포트(11434 등)를 막을 수 있다:
   `sudo firewall-cmd --add-port=11434/tcp --permanent && sudo firewall-cmd --reload`.

---

## 8. 트러블슈팅

| 증상 | 원인 | 조치 |
|---|---|---|
| `could not select device driver "" with capabilities: [[gpu]]` | Container Toolkit 미설치/미구성 | §4 재실행 후 `systemctl restart docker` |
| `docker: Error ... --gpus` | Docker 구버전/런타임 미등록 | `nvidia-ctk runtime configure`, docker 재시작 |
| `nvidia-smi` 컨테이너 안에서 실패 | 드라이버 미설치/재부팅 안 함 | §2, 재부팅 |
| ollama 가 CPU 로만 돎 | `--gpus` 누락 or toolkit 문제 | 방법 A/B로 GPU 부여, `docker logs` 확인 |
| 재부팅 후 nvidia-smi 깨짐 | 커널 업데이트로 모듈 불일치 | Ubuntu `apt install --reinstall nvidia-driver-*` / Rocky `dkms autoinstall` (DKMS 재빌드) |
| (Rocky) nvidia-smi 안 됨 | Secure Boot 미서명 모듈 차단 | `mokutil --sb-state` 확인 → Secure Boot off (§7-B) |
| (Rocky) 컨테이너 device/permission denied | SELinux enforcing | `--security-opt label=disable` 또는 볼륨 `:Z` (§7-B) |

---

## 9. 요약 체크리스트
1. `nvidia-smi` (호스트) OK  →  드라이버 ✅
2. `docker run hello-world` OK  →  Docker ✅
3. `docker run --rm --gpus all <cuda|ollama> nvidia-smi` OK  →  Toolkit ✅
4. `docker ... --gpus all ollama/ollama` + `ollama list` 에 qwen3:14b  →  LLM GPU 실행 ✅

관련: [`RUNBOOK_설치.md`](RUNBOOK_설치.md)(전체 설치), [`README.md`](../README.md) §GPU.
