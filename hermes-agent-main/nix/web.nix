# nix/web.nix — Takyon Web Dashboard (Vite/React) frontend build
{ pkgs, takyonNpmLib, ... }:
let
  src = ../web;
  npmDeps = pkgs.fetchNpmDeps {
    inherit src;
    hash = "sha256-GxSmEpclOwmv94KmGMediPITxqXAsxqTEQOoDIbYkUw=";
  };

  npm = takyonNpmLib.mkNpmPassthru { folder = "web"; attr = "web"; pname = "takyon-web"; };

  packageJson = builtins.fromJSON (builtins.readFile (src + "/package.json"));
  version = packageJson.version;
in
pkgs.buildNpmPackage (npm // {
  pname = "takyon-web";
  inherit src npmDeps version;

  doCheck = false;

  buildPhase = ''
    npx tsc -b
    npx vite build --outDir dist
  '';

  installPhase = ''
    runHook preInstall
    cp -r dist $out
    runHook postInstall
  '';
})
