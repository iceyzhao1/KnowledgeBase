#!/bin/bash
# 本地构建 Docker 镜像并导出镜像文件
#
# 使用方法：
#   bash deploy-build.sh
#
# 指定 LibreOffice 离线安装包：
#   LIBREOFFICE_DEBS=/path/to/libreoffice-debs.tar.gz bash deploy-build.sh

set -e

# Docker 镜像名称
IMAGE_NAME="coremasterkb-app:latest"

# 导出的镜像文件名称
IMAGE_TAR="cmkb.tar"

# 第三方离线依赖存放目录
VENDOR_DIR="docker/vendor"

# LibreOffice 离线安装包在项目中的目标路径
VENDOR_LIBREOFFICE="$VENDOR_DIR/libreoffice-debs.tar.gz"

# 兼容 Docker Compose V2 和旧版 docker-compose
compose() {
    if docker compose version >/dev/null 2>&1; then
        docker compose "$@"
        return 0
    fi

    if command -v docker-compose >/dev/null 2>&1; then
        docker-compose "$@"
        return 0
    fi

    echo "错误：未找到 Docker Compose。请安装 Docker Compose V2（docker compose）或旧版 docker-compose。"
    exit 1
}

# 查找 LibreOffice 离线安装包
find_libreoffice_debs() {
    # 优先使用环境变量指定的路径
    if [ -n "${LIBREOFFICE_DEBS:-}" ] && [ -f "$LIBREOFFICE_DEBS" ]; then
        echo "$LIBREOFFICE_DEBS"
        return 0
    fi

    # 尝试从常见的 D 盘挂载路径中查找
    for candidate in \
        "D:/libreoffice-debs.tar.gz" \
        "/d/libreoffice-debs.tar.gz" \
        "/mnt/d/libreoffice-debs.tar.gz"
    do
        if [ -f "$candidate" ]; then
            echo "$candidate"
            return 0
        fi
    done

    return 1
}

echo "=== 正在准备 LibreOffice 离线安装包 ==="
mkdir -p "$VENDOR_DIR"

if [ -f "$VENDOR_LIBREOFFICE" ]; then
    echo "使用已有的离线安装包：$VENDOR_LIBREOFFICE"
else
    if source_package="$(find_libreoffice_debs)"; then
        echo "正在复制离线安装包：$source_package -> $VENDOR_LIBREOFFICE"
        cp "$source_package" "$VENDOR_LIBREOFFICE"
    else
        echo "错误：未找到 LibreOffice 离线安装包。"
        echo "请将安装包放置到 $VENDOR_LIBREOFFICE，"
        echo "或者通过环境变量指定路径：LIBREOFFICE_DEBS=/path/to/libreoffice-debs.tar.gz"
        exit 1
    fi
fi

echo "=== 正在构建 Docker 镜像 ==="
compose build

echo "=== 正在导出 Docker 镜像 ==="
docker save "$IMAGE_NAME" -o "$IMAGE_TAR"

echo "=== 构建和导出已完成 ==="
ls -lh "$IMAGE_TAR"

echo "请将 $IMAGE_TAR 上传到服务器，然后执行：bash deploy-server.sh"

