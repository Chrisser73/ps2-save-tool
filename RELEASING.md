# Releasing

The desktop and web editions are independent releases. Use Semantic Versioning: increment the patch version for compatible fixes, the minor version for compatible features, and the major version for breaking changes.

## Desktop release

1. Add the finished desktop changes under **Desktop app → Unreleased** in `CHANGELOG.md`.
2. Update `APP_VERSION` in `ps2_save_tool.py`.
3. Run `make preflight` (or the equivalent Windows commands in the README).
4. Move the Unreleased notes into a dated `### [Desktop x.y.z]` entry and add its release-link definition at the bottom: `[Desktop x.y.z]: https://github.com/Chrisser73/ps2-save-tool/releases/tag/desktop-vx.y.z`.
5. Commit the release, then create and push the annotated tag:

   ```bash
   git tag -a desktop-vX.Y.Z -m "Desktop vX.Y.Z"
   git push origin main desktop-vX.Y.Z
   ```

## Web release

1. Add the finished web changes under **Web app → Unreleased** in `CHANGELOG.md`.
2. Update `web/VERSION` and `web/frontend/package.json` to the same version.
3. Run `make preflight`.
4. Move the Unreleased notes into a dated `### [Web x.y.z]` entry and add its release-link definition at the bottom: `[Web x.y.z]: https://github.com/Chrisser73/ps2-save-tool/releases/tag/web-vx.y.z`.
5. Commit the release, then create and push the annotated tag:

   ```bash
   git tag -a web-vX.Y.Z -m "Web vX.Y.Z"
   git push origin main web-vX.Y.Z
   ```

The release workflow validates the tag against the relevant version, reruns the preflight checks, and creates the matching GitHub Release. A `web-vX.Y.Z` tag also creates immutable `X.Y.Z` Docker image tags; a desktop-only release therefore never changes the web image version, and a web-only release does not imply a new Windows executable.
