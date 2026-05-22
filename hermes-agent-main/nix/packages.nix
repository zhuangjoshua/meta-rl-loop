# nix/packages.nix — Takyon Agent package built with uv2nix
{ inputs, ... }:
{
  perSystem =
    { pkgs, inputs', ... }:
    let
      takyonAgent = pkgs.callPackage ./takyon-agent.nix {
        inherit (inputs) uv2nix pyproject-nix pyproject-build-systems;
        npm-lockfile-fix = inputs'.npm-lockfile-fix.packages.default;
        # Only embed clean revs — dirtyRev doesn't represent any upstream
        # commit, so comparing it would always claim "update available".
        rev = inputs.self.rev or null;
      };
    in
    {
      packages = {
        default = takyonAgent;
        tui = takyonAgent.takyonTui;
        web = takyonAgent.takyonWeb;

        fix-lockfiles = takyonAgent.takyonNpmLib.mkFixLockfiles {
          packages = [ takyonAgent.takyonTui takyonAgent.takyonWeb ];
        };
      };
    };
}
