/** Decode a user-selected source file without changing imported event fields. */
export function decodeImportFile(bytes: ArrayBuffer, format: "csv" | "json") {
  if (bytes.byteLength > 2_000_000)
    throw new Error("Размер файла не должен превышать 2 МБ");
  let content: string;
  let encoding: "UTF-8" | "Windows-1251" = "UTF-8";
  try {
    content = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    if (format !== "csv") throw new Error("Сохраните JSON в кодировке UTF-8");
    try {
      content = new TextDecoder("windows-1251", { fatal: true }).decode(bytes);
      encoding = "Windows-1251";
    } catch {
      throw new Error(
        "Не удалось прочитать CSV. Сохраните его в UTF-8 или Windows-1251",
      );
    }
  }
  if (
    [...content].some((character) => {
      const code = character.charCodeAt(0);
      return code < 32 && code !== 9 && code !== 10 && code !== 13;
    })
  ) {
    throw new Error(
      "Выберите текстовый файл CSV или JSON. Файл Excel .xlsx сначала сохраните как CSV",
    );
  }
  if (!content.trim())
    throw new Error("Файл пуст. Выберите CSV или JSON с событиями");
  return { content, encoding };
}
