# 런북 — 베어본 서버에서 Docker GPU 사용 환경 설정 (LLM 추론용)

> OS만 깔린 서버에서 **컨테이너가 NVIDIA GPU를 쓰게** 하는 절차. LLM(qwen3:14b, q4_K_M 9.3GB)은
> GPU가 사실상 필수(16GB GPU 권장). 임베딩(bge-m3)·rag·shock 은 CPU라 이 설정 불필요.
> 기준: Ubuntu 22.04 LTS + NVIDIA GPU. (다른 배포판은 패키지 관리자만 치환)

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
```bash
sudo apt update
sudo ubuntu-drivers autoinstall            # 권장(자동 매칭). 또는 특정 버전:
# sudo apt install -y nvidia-driver-550-server
sudo reboot
# 재부팅 후 검증
nvidia-smi                                 # Driver Version / CUDA Version 표시되면 성공
```
> ollama/CUDA 12.x 를 위해 **드라이버 535 이상** 권장. 드라이버는 커널 모듈이라 커널 업데이트
> 후 재빌드가 필요할 수 있다(DKMS). 서버는 `-server` 드라이버 계열 권장.

## 3. Docker 설치 (없을 때)
```bash
docker --version || curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && newgrp docker   # sudo 없이 docker 쓰려면(재로그인)
docker run --rm hello-world
```

## 4. NVIDIA Container Toolkit 설치·구성 ★ 핵심
호스트 드라이버를 컨테이너에 연결하는 런타임. **이게 없으면 GPU 컨테이너 불가.**
```bash
# 저장소 등록
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

sudo apt update
sudo apt install -y nvidia-container-toolkit

# Docker 데몬에 nvidia 런타임 등록 (/etc/docker/daemon.json 자동 수정)
sudo nvidia-ctk runtime configure --runtime=docker
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

## 7. 에어갭(인터넷 차단) 주의

§2·§4 의 apt 설치는 인터넷이 필요하다. 차단 환경이면 **연결 구간에서 .deb 를 미리 받아** 반입:
```bash
# (연결 구간) 드라이버 + toolkit .deb 및 의존성까지 받기
sudo apt install --download-only -y nvidia-driver-550-server nvidia-container-toolkit
cp /var/cache/apt/archives/*.deb ./gpu-debs/       # 매체로 이동
# (대상 서버) 오프라인 설치
sudo dpkg -i ./gpu-debs/*.deb || sudo apt install -f
sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker
```
> 드라이버 커널 모듈은 **대상 서버 커널 버전과 맞아야** 한다. 대상과 동일 커널의 연결 구간에서
> 받거나, `.run` 러너(NVIDIA-Linux-x86_64-*.run)를 반입해 로컬 빌드하는 방법도 있다.

---

## 8. 트러블슈팅

| 증상 | 원인 | 조치 |
|---|---|---|
| `could not select device driver "" with capabilities: [[gpu]]` | Container Toolkit 미설치/미구성 | §4 재실행 후 `systemctl restart docker` |
| `docker: Error ... --gpus` | Docker 구버전/런타임 미등록 | `nvidia-ctk runtime configure`, docker 재시작 |
| `nvidia-smi` 컨테이너 안에서 실패 | 드라이버 미설치/재부팅 안 함 | §2, 재부팅 |
| ollama 가 CPU 로만 돎 | `--gpus` 누락 or toolkit 문제 | 방법 A/B로 GPU 부여, `docker logs` 확인 |
| 재부팅 후 nvidia-smi 깨짐 | 커널 업데이트로 모듈 불일치 | `sudo apt install --reinstall nvidia-driver-*` (DKMS 재빌드) |

---

## 9. 요약 체크리스트
1. `nvidia-smi` (호스트) OK  →  드라이버 ✅
2. `docker run hello-world` OK  →  Docker ✅
3. `docker run --rm --gpus all <cuda|ollama> nvidia-smi` OK  →  Toolkit ✅
4. `docker ... --gpus all ollama/ollama` + `ollama list` 에 qwen3:14b  →  LLM GPU 실행 ✅

관련: [`RUNBOOK_설치.md`](RUNBOOK_설치.md)(전체 설치), [`README.md`](../README.md) §GPU.
