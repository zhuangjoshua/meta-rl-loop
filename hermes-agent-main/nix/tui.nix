# nix/tui.nix — Takyon TUI (Ink/React) compiled with tsc and bundled
{ pkgs, takyonNpmLib, ... }:
let
  src = ../ui-tui;
  npmDeps = pkgs.fetchNpmDeps {
    inherit src;
    hash = "sha256-dNL/J4tyQQ7Ji3xfIE5b5Jdi6rQyCFjqYpzLYftJVdc=";
  };

  npm = takyonNpmLib.mkNpmPassthru { folder = "ui-tui"; attr = "tui"; pname = "takyon-tui"; };

  packageJson = builtins.fromJSON (builtins.readFile (src + "/package.json"));
  version = packageJson.version;
in
pkgs.buildNpmPackage (npm // {
  pname = "takyon-tui";
  inherit src npmDeps version;

  doCheck = false;
  npmFlags = [ "--legacy-peer-deps" ];

  installPhase = ''
    runHook preInstall

    mkdir -p $out/lib/takyon-tui

    # Single self-contained bundle built by scripts/build.mjs (esbuild).
    cp -r dist $out/lib/takyon-tui/dist

    # package.json kept for "type": "module" resolution on `node dist/entry.js`.
    cp package.json $out/lib/takyon-tui/

    runHook postInstall
  '';
})
