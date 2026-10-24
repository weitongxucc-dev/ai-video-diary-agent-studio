"use client";

// shadcn/ui Base Nova Popover (MIT), from the reviewed candidate source.
// Retain Base UI positioning, dismissal and focus behavior; style with desktop tokens.
import { Popover as Primitive } from "@base-ui/react/popover";

export const Popover = Primitive.Root;
export const PopoverTrigger = Primitive.Trigger;
export const PopoverTitle = Primitive.Title;
export const PopoverClose = Primitive.Close;

export function PopoverContent({ side = "top", align = "start", sideOffset = 8, ...props }: Primitive.Popup.Props &
  Pick<Primitive.Positioner.Props, "side" | "align" | "sideOffset">) {
  return <Primitive.Portal><Primitive.Positioner side={side} align={align} sideOffset={sideOffset} className="assistant-popover-positioner">
    <Primitive.Popup {...props}/>
  </Primitive.Positioner></Primitive.Portal>;
}
