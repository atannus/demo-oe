import { Prop, Schema, SchemaFactory } from '@nestjs/mongoose';
import { HydratedDocument } from 'mongoose';

export type OutboxDocument = HydratedDocument<Outbox>;

@Schema({ collection: 'outbox', timestamps: false })
export class Outbox {
  @Prop({ required: true })
  data_id: string;

  @Prop({ required: true })
  x: number;

  @Prop({ required: true })
  y: number;

  @Prop({ required: true })
  updated_at: Date;

  @Prop({ required: true, default: () => new Date() })
  created_at: Date;
}

export const OutboxSchema = SchemaFactory.createForClass(Outbox);
