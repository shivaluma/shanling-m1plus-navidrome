FROM m1plus-qemu:local
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends clang libclang-rt-14-dev libcurl4-openssl-dev python3 && rm -rf /var/lib/apt/lists/*
ENV ND_SANITIZERS=address,undefined
WORKDIR /work
CMD ["python3", "tests/native-host/run.py"]
