{ pkgs, ... }:
{
  packages = [ pkgs.google-chrome ];
  env.BROWSER_PATH = pkgs.lib.getExe pkgs.google-chrome;

  languages.python = {
    enable = true;
    version = "3.12";
    libraries = with pkgs; [ zlib ];
    uv = {
      enable = true;
      sync = {
        enable = true;
        allExtras = true;
      };
    };
  };
}
