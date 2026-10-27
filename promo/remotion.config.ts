// Remotion settings shared by `npm run studio` and every render script.
import {Config} from '@remotion/cli/config';

// The Chromium that ships with this machine's Playwright. Elsewhere, delete
// this line and Remotion downloads its own headless shell.
const HEADLESS = '/opt/pw-browsers/chromium_headless_shell-1194/chrome-linux/headless_shell';
import fs from 'node:fs';
if (fs.existsSync(HEADLESS)) Config.setBrowserExecutable(HEADLESS);

Config.setVideoImageFormat('png');      // lossless frames into the encoder
Config.setPixelFormat('yuv420p');       // what every phone and platform plays
Config.setCodec('h264');
Config.setCrf(14);                      // high bitrate master
Config.setConcurrency(4);
Config.setChromiumOpenGlRenderer('swiftshader');
