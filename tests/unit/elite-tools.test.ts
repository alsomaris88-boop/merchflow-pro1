import assert from "node:assert/strict";
import test from "node:test";
import { PRODUCTS } from "../../src/lib/elite-tools";

test("T-shirt product canvas uses the current 180px top collar margin", () => {
  const shirt = PRODUCTS.find((product) => product.key === "tshirt");
  assert.ok(shirt);
  assert.equal(shirt.topMargin, 180);
  assert.match(shirt.note, /180px collar margin/);
});
