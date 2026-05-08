import { Prop, Schema, SchemaFactory } from '@nestjs/mongoose';
import { HydratedDocument } from 'mongoose';

export type PositionDocument = HydratedDocument<Position>;

@Schema({ collection: 'positions' })
export class Position {
  @Prop({ required: true })
  data_id: string;

  @Prop({ required: true })
  x: number;

  @Prop({ required: true })
  y: number;

  @Prop({ required: true })
  updated_at: Date;
}

export const PositionSchema = SchemaFactory.createForClass(Position);
PositionSchema.index({ data_id: 1 }, { unique: true });
