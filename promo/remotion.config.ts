// Remotion settings shared by `npm run studio` and every render script.
import {Config} from '@remotion/cli/config';

// The Chromium that ships with this machine's Playwright, whichever build it
// is (the folder is named after the build and its layout has changed between
// them). Elsewhere none is found and Remotion downloads its own headless shell.
import fs from 'node:fs';
import path from 'node:path';
export const findHeadless = (): string | undefined => {
  const root = '/opt/pw-browsers';
  if (!fs.existsSync(root)) return undefined;
  for (const dir of fs.readdirSync(root).filter((d) => d.startsWith('chromium_headless_shell-')).sort().reverse()) {
    for (const rel of ['chrome-headless-shell-linux64/chrome-headless-shell', 'chrome-linux/headless_shell']) {
      const exe = path.join(root, dir, rel);
      if (fs.existsSync(exe)) return exe;
    }
  }
  return undefined;
};
const HEADLESS = findHeadless();
if (HEADLESS) Config.setBrowserExecutable(HEADLESS);

Config.setVideoImageFormat('png');      // lossless frames into the encoder
Config.setPixelFormat('yuv420p');       // what every phone and platform plays
Config.setCodec('h264');
Config.setCrf(14);                      // high bitrate master
Config.setConcurrency(4);
Config.setChromiumOpenGlRenderer('swiftshader');
