import { expect, test } from "vitest";
import { decodeImportFile } from "../src/lib/import-file";

test("CSV from Russian Excel is decoded without losing Cyrillic", () => {
  const prefix = new TextEncoder().encode("title,city\nTEST_DATA,");
  const bytes = new Uint8Array([...prefix, 0xd3, 0xf4, 0xe0]);
  expect(decodeImportFile(bytes.buffer, "csv")).toEqual({
    content: "title,city\nTEST_DATA,Уфа",
    encoding: "Windows-1251",
  });
});

test("UTF-8 and BOM preserve quoted CSV and multiline content", () => {
  const content = 'title;description\n"TEST_DATA";"Уфа, театр\nвечером"';
  const bytes = new TextEncoder().encode("\ufeff" + content);
  expect(decodeImportFile(bytes.buffer, "csv")).toEqual({
    content,
    encoding: "UTF-8",
  });
});

test("JSON requires UTF-8 and binary or oversized files cannot be imported", () => {
  expect(() =>
    decodeImportFile(new Uint8Array([0xd3, 0xf4, 0xe0]).buffer, "json"),
  ).toThrow(/UTF-8/);
  expect(() =>
    decodeImportFile(new Uint8Array([65, 0, 66, 0]).buffer, "csv"),
  ).toThrow(/текстовый/);
  expect(() => decodeImportFile(new ArrayBuffer(2_000_001), "csv")).toThrow(
    /2 МБ/,
  );
});
