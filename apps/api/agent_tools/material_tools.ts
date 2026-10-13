/** Stable public registration entry; selection/CAS expectation has one owner. */
import type {MaterialToolsContext} from './materials/contracts.ts';
import type {ToolRegistrar} from './diary/contracts.ts';
import {createMaterialSelection} from './materials/selection.ts';
import {registerMaterialReads} from './materials/reads.ts';
import {registerMaterialTagCommands} from './materials/tags.ts';
export type {MaterialSelection, MaterialAsset, MaterialToolsContext} from './materials/contracts.ts';

export function registerMaterialTools(pi: ToolRegistrar, context: MaterialToolsContext): void {
  const {scopeLabel, boundSelection, selectedObject, verifyVersion, projection, advanceVersion} = createMaterialSelection(context.selection);
  const {request} = context;
  registerMaterialReads(pi, {request, boundSelection, selectedObject, verifyVersion, projection});
  registerMaterialTagCommands(pi, {request, scopeLabel, boundSelection, selectedObject, verifyVersion, projection, advanceVersion});
}
