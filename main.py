from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv
import motor.motor_asyncio
from bson import ObjectId
import jwt
from jwt.exceptions import DecodeError

load_dotenv()
app = FastAPI()



# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust as needed for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



# MongoDB setup
MONGO_URI = os.getenv("DB_URI")
if not MONGO_URI:
    raise EnvironmentError("DB_URI environment variable not set")
client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
db = client["doctors-portal"]



# Collections
appointment_options_collection = db["appointmentCollection"]
booking_collection = db["bookingCollaction"]
users_collection = db["usersCollaction"]
doctors_collection = db["doctorsCollactions"]
payment_collection = db["paymentCollection"]
contact_collection = db["contactCollection"]


# JWT Secret
JWT_SECRET = os.getenv("ACCESS_TOKEN")
if not JWT_SECRET:
    raise EnvironmentError("ACCESS_TOKEN environment variable not set")
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_TIME = timedelta(days=2)



# Pydantic Models
class PyObjectId(str):
    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v, **kwargs):
        if not ObjectId.is_valid(v):
            raise ValueError("Invalid object id")
        return str(ObjectId(v))



class AppointmentOption(BaseModel):
    id: PyObjectId = None
    name: str
    slots: List[str]
    price: float


    class Config:
        json_encoders = {ObjectId: str}



class Booking(BaseModel):
    id: PyObjectId = None
    appointmentDate: str
    treatment: str
    patient: str
    slot: str
    email: str
    phone: str
    price: float

    class Config:
        json_encoders = {ObjectId: str}



class User(BaseModel):
    id: PyObjectId = None
    name: str
    email: str
    role: str

    class Config:
        json_encoders = {ObjectId: str}



class Doctor(BaseModel):
    id: PyObjectId = None
    name: str
    email: str
    img: str

    class Config:
        json_encoders = {ObjectId: str}



class Contact(BaseModel):
    id: PyObjectId = None
    name: str
    email: str
    subject: str
    message: str

    class Config:
        json_encoders = {ObjectId: str}



class PaymentBooking(BaseModel):
    id: str
    appointmentDate: str
    treatment: str
    patient: str
    slot: str
    email: str
    phone: str
    price: float



class Payment(BaseModel):
    id: PyObjectId = None
    paymentMethodId: str
    booking: PaymentBooking

    class Config:
        json_encoders = {ObjectId: str}



class TokenPayload(BaseModel):
    email: str
    exp: int









# Helper Functions
def create_jwt_token(email: str):
    payload = {"email": email, "exp": datetime.utcnow() + JWT_EXPIRATION_TIME}
    return jwt.encode(payload, JWT_SECRET, JWT_ALGORITHM)



async def get_current_user(token: str) -> str:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload.get("email")
    except DecodeError:
        raise HTTPException(status_code=401, detail="Invalid token")
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")



async def verify_admin(user_email: str = Depends(get_current_user)):
    user = await users_collection.find_one({"email": user_email})
    if not user or user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Forbidden access")
    return user_email



# Dependency for JWT verification
async def verify_jwt(authorization: str = None) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        scheme, token = authorization.split()
        if scheme.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid authentication scheme")
        return await get_current_user(token)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authorization header")



# API Endpoints
@app.post("/contact", response_model=Contact)
async def handle_contact_post(contact: Contact):
    result = await contact_collection.insert_one(contact.dict())
    inserted_id = result.inserted_id
    return await contact_collection.find_one({"_id": inserted_id})



@app.get("/appointmentOptions", response_model=List[AppointmentOption])
async def handle_get_appointment_options(date: str):
    options_cursor = appointment_options_collection.find({})
    options = await options_cursor.to_list(length=None)

    already_booked_cursor = booking_collection.find({"appointmentDate": date})
    already_booked = await already_booked_cursor.to_list(length=None)

    for option in options:
        booked_slots = [book["slot"] for book in already_booked if book["treatment"] == option["name"]]
        remaining_slots = [slot for slot in option["slots"] if slot not in booked_slots]
        option["slots"] = remaining_slots

    return [AppointmentOption(**opt) for opt in options]



@app.get("/v2/appointmentOptions", response_model=List[AppointmentOption])
async def handle_get_v2_appointment_options(data: str):
    pipeline = [
        {
            "$lookup": {
                "from": "bookingCollaction",
                "localField": "name",
                "foreignField": "treatment",
                "pipeline": [
                    {"$match": {"$expr": {"$eq": ["$appointmentDate", data]}}},
                ],
                "as": "booked",
            }
        },
        {"$project": {"name": 1, "slots": 1, "price": 1, "booked": {"$map": {"input": "$booked", "as": "book", "in": "$$book.slot"}}}},
        {"$project": {"name": 1, "price": 1, "slots": {"$setDifference": ["$slots", "$booked"]}}},
    ]
    options_cursor = appointment_options_collection.aggregate(pipeline)
    options = await options_cursor.to_list(length=None)
    return [AppointmentOption(**opt) for opt in options]




@app.get("/bookings", response_model=List[Booking])
async def handle_get_bookings(email: str, current_user_email: str = Depends(get_current_user)):
    if email != current_user_email:
        raise HTTPException(status_code=403, detail="Forbidden")
    bookings_cursor = booking_collection.find({"email": email})
    bookings = await bookings_cursor.to_list(length=None)
    return [Booking(**booking) for booking in bookings]




@app.get("/bookings/{id}", response_model=Booking)
async def handle_get_booking_by_id(id: PyObjectId):
    booking = await booking_collection.find_one({"_id": ObjectId(id)})
    if booking:
        return Booking(**booking)
    raise HTTPException(status_code=404, detail="Booking not found")




@app.post("/bookings", response_model=Booking)
async def handle_post_booking(booking: Booking):
    existing_booking = await booking_collection.find_one({
        "appointmentDate": booking.appointmentDate,
        "email": booking.email,
        "treatment": booking.treatment,
    })
    if existing_booking:
        raise HTTPException(status_code=200, detail=f"You already have a booking on {booking.appointmentDate}")

    result = await booking_collection.insert_one(booking.dict())
    inserted_id = result.inserted_id
    return await booking_collection.find_one({"_id": inserted_id})
    # TODO: Implement sendBookingEmail function



@app.post("/create-payment-intent")
async def handle_create_payment_intent(booking: Booking):
    price = booking.price
    amount = int(price * 100)
    # TODO: Integrate with Stripe to create a payment intent
    # Placeholder for demonstration
    print(f"Creating payment intent for amount: {amount}")
    client_secret = "test_client_secret"
    return {"clientSecret": client_secret}



@app.post("/payments", response_model=Payment)
async def handle_post_payment(payment: Payment):
    result = await payment_collection.insert_one(payment.dict())
    inserted_id = result.inserted_id
    # TODO: Update booking status to paid if needed
    return await payment_collection.find_one({"_id": inserted_id})




@app.get("/jwt")
async def handle_get_jwt(email: str):
    user = await users_collection.find_one({"email": email})
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    access_token = create_jwt_token(email)
    return {"accessToken": access_token}



@app.get("/appointmentSpecialty", response_model=List[str])
async def handle_get_appointment_specialty():
    specialties = await appointment_options_collection.distinct("name")
    return specialties



@app.get("/users", response_model=List[User], dependencies=[Depends(verify_jwt), Depends(verify_admin)])
async def handle_get_users():
    users_cursor = users_collection.find({})
    users = await users_cursor.to_list(length=None)
    return [User(**user) for user in users]



@app.post("/users", response_model=User)
async def handle_post_user(user: User):
    result = await users_collection.insert_one(user.dict())
    inserted_id = result.inserted_id
    return await users_collection.find_one({"_id": inserted_id})



@app.get("/users/admin/{email}")
async def handle_get_user_admin_by_email(email: str):
    user = await users_collection.find_one({"email": email})
    if user and user.get("role") == "admin":
        return {"isAdmin": True}
    return {"isAdmin": False}



@app.put("/users/admin/{id}", response_model=dict, dependencies=[Depends(verify_jwt), Depends(verify_admin)])
async def handle_put_user_admin_by_id(id: PyObjectId):
    result = await users_collection.update_one({"_id": ObjectId(id)}, {"$set": {"role": "admin"}}, upsert=True)
    return {"acknowledged": result.acknowledged, "modified_count": result.modified_count, "upserted_id": str(result.upserted_id)}



@app.get("/doctors", response_model=List[Doctor], dependencies=[Depends(verify_jwt), Depends(verify_admin)])
async def handle_get_doctors():
    doctors_cursor = doctors_collection.find({})
    doctors = await doctors_cursor.to_list(length=None)
    return [Doctor(**doctor) for doctor in doctors]



@app.delete("/doctors/{id}", response_model=dict, dependencies=[Depends(verify_jwt), Depends(verify_admin)])
async def handle_delete_doctor_by_id(id: PyObjectId):
    result = await doctors_collection.delete_one({"_id": ObjectId(id)})
    return {"acknowledged": result.acknowledged, "deleted_count": result.deleted_count}



@app.post("/doctors", response_model=Doctor, dependencies=[Depends(verify_jwt), Depends(verify_admin)])
async def handle_post_doctor(doctor: Doctor):
    result = await doctors_collection.insert_one(doctor.dict())
    inserted_id = result.inserted_id
    return await doctors_collection.find_one({"_id": inserted_id})






if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))