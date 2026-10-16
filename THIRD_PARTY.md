# Third-party components and technical references

## mymc++

Used at runtime for PS2 memory-card filesystem operations (`.ps2`, `.mc2`, compatible `.mcd`/raw images).

- Project: https://github.com/Adubbz/mymcplusplus
- License: GNU GPL v3
- Package: `mymcplusplus`

The source of mymc++ is not copied into this project.

## Pillow

Used for local PS1/PS2 icon preview rendering.

## cryptography

Used as the default AES backend for PSV/VMP signing and verification. The code also accepts PyCryptodome when it is already installed.

## Format references

The local parsers/writers were implemented from public format documentation and open-source interoperability references, including PS2 save/icon documentation and Apollo Save Tool's public PS1/PSV format handling. Those projects are references only; no Sony SDK or proprietary Sony executable is included.
