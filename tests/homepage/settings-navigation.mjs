/** Navigate the production settings UI without depending on translated labels. */
export const closeDialog=page=>page.locator('.dialog-header [data-close]').click();
export async function openPageSettings(page) {
  if(await page.locator('[data-settings-done]').count())await closeDialog(page);
  await page.locator('#settings-button').click();
}
export async function openShortcutSettings(page) {
  await openPageSettings(page);await page.locator('#shortcut-settings').click();
}
export async function openAppearanceSettings(page) {
  await openPageSettings(page);await page.locator('#appearance-settings').click();
}
export async function openBackgroundSettings(page) {
  await openAppearanceSettings(page);await page.locator('#background-settings').click();
}
export async function openTransferSettings(page) {
  await openPageSettings(page);await page.locator('#data-settings').click();
}
export const setShortcutSize=(page,value)=>page.locator('[data-shortcut-size="'+value+'"]').click();
