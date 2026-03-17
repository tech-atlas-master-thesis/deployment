#!/usr/bin/env bash

declare -A SERVICES

# Discover compose folders
while IFS= read -r -d '' file; do
    dir=$(dirname "$file")
    id=$(basename "$dir")
    SERVICES["$id"]="$dir"
done < <(find . -mindepth 2 -maxdepth 2 -name docker-compose.yml -print0)

start_one() {
    local id="$1"
    local dir="${SERVICES[$id]}"

    if [[ -z "$dir" ]]; then
        echo "Error: Unknown service '$id'"
        return 1
    fi

    echo "Starting $id ($dir)"

    (
        cd "$dir" || exit 1

        echo "Pulling images..."
        docker compose pull
        if [[ $? -ne 0 ]]; then
            echo "Failed to pull images for $id"
            exit 1
        fi

        echo "Starting containers..."
        docker compose up -d
    )

    if ! docker compose pull; then
        echo "Failed to pull images for $id"
        exit 1
    fi
}

start_all() {
    local failed=0

    for id in "${!SERVICES[@]}"; do
        start_one "$id" || failed=1
    done

    return $failed
}

# No argument → start everything
if [[ -z "$1" ]]; then
    start_all
    exit $?
fi

# Explicit all
if [[ "$1" == "all" ]]; then
    start_all
    exit $?
else
    # Start specific stack
    start_one "$1"
fi

exit $?