#!/usr/bin/env bash
# Map profile id → GitHub Pages subfolder (one repo, two apps).
pages_path_for_profile() {
  case "$1" in
    gardner-farm) echo "skout" ;;
    kate-vehicles) echo "auto-skout" ;;
    kate-art) echo "art" ;;
    *) echo "$1" ;;
  esac
}

app_label_for_profile() {
  case "$1" in
    gardner-farm) echo "Skout — farm & free stuff" ;;
    kate-vehicles) echo "Auto Skout — vehicles & trucks" ;;
    kate-art) echo "Art Scout — grants & opportunities" ;;
    *) echo "$1" ;;
  esac
}

write_pages_root_index() {
  local root="$1"
  cat > "$root/index.html" <<'EOF'
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Skout apps</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 32rem; margin: 3rem auto; padding: 0 1rem; color: #292524; }
    h1 { font-size: 1.35rem; }
    a { display: block; padding: .85rem 1rem; margin: .5rem 0; border: 1px solid #d6d3d1; border-radius: 10px;
      text-decoration: none; color: #166534; font-weight: 600; }
    a:hover { background: #f0fdf4; }
    p { color: #78716c; font-size: .9rem; }
  </style>
</head>
<body>
  <h1>Choose your dashboard</h1>
  <p>Skout and Auto Skout are separate apps — each link opens its own feed.</p>
  <a href="skout/">Skout — farm &amp; free stuff</a>
  <a href="auto-skout/">Auto Skout — vehicles &amp; trucks</a>
  <a href="art/">Art Scout — grants &amp; opportunities</a>
</body>
</html>
EOF
  touch "$root/.nojekyll"
}

clean_legacy_pages_root() {
  local root="$1"
  rm -rf "$root/assets" "$root/data.json" "$root/.DS_Store"
}
